---
name: vasp-sop-serial-system-loop
description: "隔离/切换 vasp-sop 单体系串行 loop（systemd service 的 --exclude 9 个体系），含冒号体系名引号与 stop 超时坑。用于\"只跑 X 体系/推进下一个体系\"时。"
---

# vasp-sop 单体系串行 loop 隔离与切换

用户 2026-08-10 定：单体系串行推进（完成一个含 wave3 再下一个），不批量铺开。loop 常驻 `~/.config/systemd/user/vasp-sop-loop.service`。

## 当前形态（隔离到某体系）

```
ExecStart=.../vasp-sop batch run <2026根> --loop --poll 120 \
  --exclude BaAl2B2O7 --exclude BaAl4O7 --exclude 'Gd2GaSbO7:Bi' \
  --exclude La2SrSc2O7 --exclude La2Zr2O7 --exclude SrAl4O7 \
  --exclude 'SrGa4O7:Fe' --exclude Y2Sn2O7 --exclude Y2Ti2O7
```

## 切换流程（推进下一个体系）

1. `systemctl --user stop vasp-sop-loop` —— **注意**：若在提交中会卡 deactivating 90s（TimeoutStopUSec），等它 failed/dead 再改，不要并行 sed（会丢改动）
2. 用 python 改 service（**不要 sed**——引号/长行容易坏）：把目标体系的 `--exclude X` 从 ExecStart 删掉；描述行更新
3. `systemctl --user daemon-reload && systemctl --user start vasp-sop-loop`
4. 验证：`ps -o args= -p $(systemctl --user show vasp-sop-loop -p MainPID --value) | grep -o "exclude [^ ]*"` + 日志 `grep "Batch run" <2026根>/batch_run.log` 应显示 `1 systems`

## 坑

- **冒号体系名**（Gd2GaSbO7:Bi、SrGa4O7:Fe）：systemd ExecStart 里必须单引号包裹，否则 systemd 把冒号后的当 prefix 解析失败
- **stop 超时**：stop 请求后旧进程可能还在跑一轮（最多 90s），期间改 service 会被 systemd 覆盖/丢失——等 `is-active` 非 deactivating 再改
- **切换后记录**：docs/next-actions.md 追加当前隔离体系与顺序（CaAl4O7 → SrAl4O7 → Gd2GaSbO7:Bi → La2Zr2O7 → Y2Sn2O7 → La2SrSc2O7 → Y2Ti2O7）
- 其他体系在跑的尾巴作业（上一波残留）**不 cancel**——跑完自然结束
- 顺序决策（ADR 0014 / next-actions）：两阶段 SOC 机制（stage2_soc plan 开关）已实现未实施，切换 SOC 体系前先与用户确认
