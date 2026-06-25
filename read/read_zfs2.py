import json
from tqdm import tqdm
import os
import numpy as np
from pymatgen.io.vasp import Xdatcar, Outcar, Vasprun

class modified_Outcar(Outcar):
    def __init__(self, filename):
        super().__init__(filename)
    
    def read_energy(self) -> None:
        """free  energy   TOTEN  =     -2050.18146010 eV
        """
        pattern = {"step_energy": r"free  energy   TOTEN\s+=\s+([\d\-\.]+)"}
        self.read_pattern(pattern, postprocess=float, terminate_on_match=False)

    def read_zero_field_splitting(self):
        """Read spin-spin contribution to zero-field splitting tensor data.

        Output structure:
            self.data["zero_field_splitting"] = {
                "ss_tensor": [D_xx, D_yy, D_zz, D_xy, D_xz, D_yz],
                "diagonalized": [
                    [D_diag1, vec_x1, vec_y1, vec_z1],
                    [D_diag2, vec_x2, vec_y2, vec_z2],
                    [D_diag3, vec_x3, vec_y3, vec_z3]
                ]
            }
        """
        # 第一段表格：原始张量分量
        header_pattern1 = (
            r"Spin-spin contribution to zero-field splitting tensor \(MHz\)"
            r"\s*\-+\s*"
            r"D_xx\s+D_yy\s+D_zz\s+D_xy\s+D_xz\s+D_yz\s*"
            r"\-+"
        )
        row_pattern1 = r"\s*" + r"\s+".join([r"([-]?\d+\.\d+)"] * 6)
        footer_pattern = r"\-+"

        zfs_tensor: list[list[float]] = self.read_table_pattern(
            header_pattern1,
            row_pattern1,
            footer_pattern,
            postprocess=float,
            last_one_only=False
        )

        zfs_tensor = [i[0] for i in zfs_tensor]  # 只取第一行的6个分量

        # 第二段表格：对角化后的结果
        header_pattern2 = (
            r"after diagonalization\s*"
            r"\-+\s*"
            r"D_diag\s+eigenvector \(x,y,z\)\s*"
            r"\-+"
        )
        row_pattern2 = r"\s*" + r"\s+".join([r"([-]?\d+\.\d+)"] * 4)

        zfs_diag: list[list[float]] = self.read_table_pattern(
            header_pattern2,
            row_pattern2,
            footer_pattern,
            postprocess=float,
            last_one_only=False
        )
        
        self.data["zero_field_splitting"] = {
            "tensor":  zfs_tensor,
            "diag": zfs_diag
        }

def get_frac_coords(p):
    return np.array([site.frac_coords for site in p.sites])

def get_lattice(p):
    return p.lattice.matrix

def tom(ar):
    return [[ar[0], ar[3], ar[4]], 
            [ar[3], ar[1], ar[5]], 
            [ar[4], ar[5], ar[2]]]
total_data = []

dir_count = 0
json_count = 0
outcar_count = 0

for i in os.listdir():
    if os.path.isdir(i):
        dir_count += 1
        if os.path.isfile(f"{i}/zfs.json"):
            json_count += 1
        if os.path.isfile(f"{i}/OUTCAR"):
            outcar_count += 1
        else:
            print(f"OUTCAR file not found in directory: {i}")

print(f"Total directory count: {dir_count}")
print(f"Directory count with zfs.json: {json_count}")
print(f"Directory count with OUTCAR: {outcar_count}")        

input("Press Enter to continue...")

for i in tqdm(os.listdir()):
    tqdm.write(f"Processing directory: {i}")
    if i == "read_zfs.py" or i == "data.json":
        continue

    # if not os.path.isfile(f"{i}/relax/vasprun.xml"):
    #     continue

    # try:
    #     vasprun = Vasprun(f"{i}/relax/vasprun.xml")
    # except:
    #     continue
    
    # if not vasprun.converged_electronic:
    #     continue

    try:
        xdat = Xdatcar(f"{i}/relax/XDATCAR")
        out = modified_Outcar(f"{i}/relax/OUTCAR")
        out.read_zero_field_splitting()
        out.read_energy()
    except Exception as e:
        continue

    for t, e, p in zip(out.data["zero_field_splitting"]["tensor"], out.data['step_energy'], xdat.structures):
        total_data.append({
            "zfs_from_outcar": tom(t),
            "energy": e[0],
            "poscar": get_frac_coords(p),
        })

with open("data.json", "w") as json_file:
    json.dump(total_data, json_file)
