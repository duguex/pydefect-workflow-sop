import lmdb
import pickle
import torch
import os

"""
LMDB图数据读取示例程序
用于读取LMDB格式的图数据，显示前5个图的形状信息和张量内容
"""

# 设置LMDB文件路径（根据实际情况修改）
lmdb_path = './train_data.lmdb'

# 检查文件是否存在
if not os.path.exists(lmdb_path):
    print(f"错误：LMDB文件不存在 - {lmdb_path}")
    print("请检查文件路径是否正确，或修改代码中的lmdb_path变量")
    exit(1)

print(f"正在读取LMDB文件：{lmdb_path}")

# 打开LMDB环境
env = lmdb.open(
    lmdb_path,
    readonly=True,
    lock=False,
    readahead=False
)

try:
    # 开始事务
    with env.begin(write=False) as txn:
        # 获取数据库中的所有键
        cursor = txn.cursor()
        keys = list(cursor.iternext(keys=True, values=False))
        total_entries = len(keys)
        print(f"LMDB文件中共有 {total_entries} 个图数据")
        
        # 限制读取的条目数为前5个
        num_to_read = min(5, total_entries)
        print(f"将显示前 {num_to_read} 个图的数据信息\n")
        
        # 读取并显示前5个图数据
        for i in range(num_to_read):
            key = keys[i]
            # 从LMDB中获取数据
            data_pickle = txn.get(key)
            # 反序列化数据
            data = pickle.loads(data_pickle)
            
            print(f"===== 图 {i+1} (键: {key.decode()}) =====")
            print(f"数据类型: {type(data)}")
            
            # 检查是否为PyTorch Geometric的Data对象
            if hasattr(data, 'x') and hasattr(data, 'edge_index'):
                print(f"节点特征 (x) 形状: {data.x.shape}")
                print(f"边索引 (edge_index) 形状: {data.edge_index.shape}")
                
                # 显示节点特征的前几行
                print("节点特征 (x) 内容:")
                print(data.x[:5] if data.x.shape[0] > 5 else data.x)
                
                # 显示边索引的前几列
                print("边索引 (edge_index) 内容:")
                print(data.edge_index[:, :10] if data.edge_index.shape[1] > 10 else data.edge_index)
                
                # 如果有边属性，显示其信息
                if hasattr(data, 'edge_attr') and data.edge_attr is not None:
                    print(f"边属性 (edge_attr) 形状: {data.edge_attr.shape}")
                    print("边属性 (edge_attr) 内容:")
                    print(data.edge_attr[:5] if data.edge_attr.shape[0] > 5 else data.edge_attr)
                
                # 如果有目标值，显示其信息
                if hasattr(data, 'y') and data.y is not None:
                    print(f"目标值 (y) 形状: {data.y.shape}")
                    print(f"目标值 (y) 内容: {data.y}")
                
                # 如果有批次信息，显示其信息
                if hasattr(data, 'batch') and data.batch is not None:
                    print(f"批次信息 (batch) 形状: {data.batch.shape}")
                    print("批次信息 (batch) 内容:")
                    print(data.batch[:10] if data.batch.shape[0] > 10 else data.batch)
            else:
                print("警告：数据不是有效的PyTorch Geometric Data对象")
                print(f"数据包含的属性: {dir(data)}")
            
            print()  # 空行分隔不同的图

except Exception as e:
    print(f"读取LMDB文件时发生错误: {e}")
finally:
    # 关闭LMDB环境
    env.close()
    print("LMDB环境已关闭")