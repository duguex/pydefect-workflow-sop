---
name: vasp-sop-failed-jobs-stat-audit
description: "三层审计 vasp-sop failed 作业（记录级分类→目录去重→磁盘现状），出统计报告再谈处理策略。用户问\"failed 怎么处理/出个统计报告/重试链\"时使用。"
---

# vasp-sop failed 作业统计审计

用户问"failed 怎么处理 / 出个统计报告"时，先做记录→目录→磁盘三层审计，再谈策略。**禁止**只看 failed 计数就下结论——重试链会让记录数膨胀 10-20 倍（如 La2SrSc2O7 cpd 12 目录 161 条记录）。

## 1. 记录级分类（error_msg 特征）

```bash
sqlite3 ~/.crisp/data/agent.db "
SELECT
  CASE
    WHEN error_msg LIKE '%ZBRENT%' THEN '1_zBRENT'
    WHEN error_msg LIKE '%TIME LIMIT%' OR error_msg LIKE '%KILLED%' THEN '2_transient_kill'
    WHEN error_msg LIKE '%LREAL%' OR error_msg LIKE '%EDIFF%' THEN '3_param'
    WHEN error_msg LIKE '%POSCAR%' OR error_msg LIKE '%missing or empty%' THEN '4_input_missing'
    ELSE '5_other'
  END AS cls, COUNT(*)
FROM jobs WHERE local_dir LIKE '%<root>%' AND status='failed'
GROUP BY cls ORDER BY cls;"
```

- `5_other` 大头 = 电子不收敛（error_msg 尾部是 "1\n1\n1..." 或截断的表格行）
- EXIT_CODE 1 = VASP 自己退出（ZBRENT/不收敛）；255 = 被外部杀（瞬态）

## 2. 目录级去重（真欠账 = DISTINCT local_dir）

```bash
sqlite3 ~/.crisp/data/agent.db "
SELECT <system>, <cpd/defect/unitcell>, COUNT(DISTINCT local_dir) AS dirs, COUNT(*) AS records
FROM jobs WHERE local_dir LIKE '%<root>%' AND status='failed'
GROUP BY sys, kind ORDER BY dirs DESC;"
```

records/dirs 比值 > 3 就是重试链（persistent 失败无冷却的证据，ADR 0008 欠账）。

## 3. 磁盘级现状（历史 failed 有多少已重跑成功）

用 python（目录名含 `:` `[]` 等 glob 危险字符，bash 循环会踩坑）：对 DISTINCT local_dir 逐个 stat `OUTCAR` 是否存在 + mtime。注意：
- 有 OUTCAR ≠ 收敛（ZBRENT 失败也写 OUTCAR）——要收敛判定得看 verdict/尾部 "reached required accuracy"
- 目录不存在 = stale 记录；无 OUTCAR = 没跑过/输出被清（可能 cache 收走或重跑清输出）

## 4. 报告口径

输出：记录级分类 + 目录级分布 + 磁盘现状（live/stale/no_outcar 计数 + 最新 mtime 排序），再指出：哪些是历史（已重跑成功）、哪些是活欠账、重试链烧核时（每 2 分钟一轮）。最后才摆策略选项（分类修复参数 / 有限重试 / 接受损失 / 冷却节流）。

## 坑

- `local_dir` 含 2026_undergo_spin_defect/ 前缀，取体系名用 instr/substr 而不是 split（路径里有冒号体系名）
- 冷却节流（ZBRENT 停 6h 再试）是 ADR 0008 欠账——persistent 失败无上限重试仍在烧核时
- 大晶胞相（max_abc>25Å）被 preflight 门 skip 是另一类噪音，不在 failed 里
