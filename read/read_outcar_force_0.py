# %%
import json
from tqdm import tqdm
import os
import numpy as np
from pymatgen.io.vasp import Xdatcar, Outcar
from pymatgen.core import Structure

# %%
class modified_Outcar(Outcar):
    # {"fch": fch_table, "dh": dh_table, "th": th_table}
    # Fermi contact (isotropic) hyperfine coupling parameter (MHz)
    # -------------------------------------------------------------
    # ion      A_pw      A_1PS     A_1AE     A_1c      A_tot
    # -------------------------------------------------------------
    # 1       6.574     6.611    41.410   -11.472    41.373
    # 2      46.852    46.913   223.876   -84.927   223.814
    # 3       6.037     6.073    39.067   -10.485    39.032
    # -------------------------------------------------------------
    # Dipolar hyperfine coupling parameters (MHz)
    # ---------------------------------------------------------------------
    # ion      A_xx      A_yy      A_zz      A_xy      A_xz      A_yz
    # ---------------------------------------------------------------------
    # 1     -13.063   -13.390    26.453     4.123    11.673    12.234
    # 2      -3.949    -2.345     6.294   -69.409   -42.917   -37.636
    # 3     -13.040    26.115   -13.074    11.346     4.153    11.393
    # ---------------------------------------------------------------------
    # Total hyperfine coupling parameters after diagonalization (MHz)
    # (convention: |A_zz| > |A_xx| > |A_yy|)
    # ----------------------------------------------------------------------
    # ion      A_xx      A_yy      A_zz     asymmetry (A_yy - A_xx)/ A_zz
    # ----------------------------------------------------------------------
    # 1      25.573    23.964    74.582      -0.022
    # 2     259.673   121.375   290.395      -0.476
    # 3      23.865    21.821    71.409      -0.029
    # ---------------------------------------------------------------------
    def __init__(self, filename):
        super().__init__(filename)

    def read_energy(self) -> None:
        """free  energy   TOTEN  =     -2050.18146010 eV"""
        pattern = {"step_energy": r"free  energy   TOTEN\s+=\s+([\d\-\.]+)"}
        self.read_pattern(pattern, postprocess=float, terminate_on_match=False)

    def read_force(self):
        forces = self.read_table_pattern(
            header_pattern=r"\sPOSITION\s+TOTAL-FORCE \(eV/Angst\)\s*\-+",
            row_pattern=r"\s*[0-9]+\.[0-9]+\s+[0-9]+\.[0-9]+\s+[0-9]+\.[0-9]+\s+([0-9\-]+\.[0-9]+)\s+([0-9\-]+\.[0-9]+)\s+([0-9\-]+\.[0-9]+)",
            footer_pattern=r"\s*\-+",
            postprocess=float,
            last_one_only=True,
        )
        self.data["force"] = forces

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

        _zfs_tensor: list[list[float]] = self.read_table_pattern(
                header_pattern1,
                row_pattern1,
                footer_pattern,
                postprocess=float,
                last_one_only=True,
            )

        # 第二段表格：对角化后的结果
        # header_pattern2 = (
        #     r"after diagonalization\s*" r"\-+\s*" r"D_diag\s+eigenvector \(x,y,z\)\s*" r"\-+"
        # )
        # row_pattern2 = r"\s*" + r"\s+".join([r"([-]?\d+\.\d+)"] * 4)

        # zfs_diag: list[list[float]] = self.read_table_pattern(
        #     header_pattern2,
        #     row_pattern2,
        #     footer_pattern,
        #     postprocess=float,
        #     last_one_only=True,
        # )

        self.data["zero_field_splitting"] = _zfs_tensor[0]


def get_frac_coords(p):
    return np.array([site.frac_coords for site in p.sites]).tolist()


def get_lattice(p):
    return p.lattice.matrix


def tom(ar):
    return [[ar[0], ar[3], ar[4]], [ar[3], ar[1], ar[5]], [ar[4], ar[5], ar[2]]]

# %%
def read_zfs_path(path):
    out = modified_Outcar(f"{path}/OUTCAR")
    out.read_energy()
    out.read_force()
    pos = Structure.from_file(f"{path}/CONTCAR")

    _data =  {
        "path":path,
            "energy": out.data["step_energy"][0][0],
            "poscar": get_frac_coords(pos),
            "force": out.data["force"],
        }
    
    try:
        out.read_zero_field_splitting()
    except Exception as e:
        pass

    try:
        out.read_fermi_contact_shift()
    except Exception as e:
        pass

    if "fermi_contact_shift" in out.data:
        hf = []
        for fc, d in zip(
            out.data["fermi_contact_shift"]["fch"], out.data["fermi_contact_shift"]["dh"]
        ):
            hf.append((np.eye(3) * fc[-1] + np.array(tom(d))).tolist())
            
        fermi = [i[-1] for i in out.data["fermi_contact_shift"]["fch"]]

        _data.update(    {
            "hyperfine": hf,
            "fermi": fermi,
        })
        

    if "zero_field_splitting" in out.data:
        zfs_from_outcar = tom(out.data["zero_field_splitting"])
        _data.update({"zfs": zfs_from_outcar,}) 
    
    return _data

    # total_data.append(data)

    # print(len(total_data))

    # with open("data.json", "w") as json_file:
    # json.dump(total_data, json_file)

    # print(f"Total data points collected: {len(total_data)}")


# %%
#read_zfs_path("/mnt/shared/home/2sidesniddle/diamond/216_sample/modes/mode_3_displace_0.3.vasp")

# %%
if __name__=="__main__":

    total_data = []

    dir_count = 0
    # json_count = 0
    outcar_count = 0

    for i in os.listdir():
        if os.path.isdir(i):
            dir_count += 1
            # if os.path.isfile(f"{i}/zfs.json"):
            #     json_count += 1
            if os.path.isfile(f"{i}/OUTCAR"):
                outcar_count += 1
            else:
                print(f"OUTCAR file not found in directory: {i}")

    print(f"Total directory count: {dir_count}")
    # print(f"Directory count with zfs.json: {json_count}")
    print(f"Directory count with OUTCAR: {outcar_count}")

    input("Press Enter to continue...")

    for i in tqdm(os.listdir()):
        # if i == "read_zfs.py" or i == "data.json":
        #     continue

        # if not os.path.isfile(f"{i}/relax/vasprun.xml"):
        #     continue

        # try:
        #     vasprun = Vasprun(f"{i}/relax/vasprun.xml")
        # except:
        #     continue

        # if not vasprun.converged_electronic:
        #     continue

        try:
            # xdat = Xdatcar(f"{i}/XDATCAR")
            total_data.append(read_zfs_path(i))
        except Exception as e:
            print(f"prase failed in {i}")

    with open("data.json", "w") as json_file:
        json.dump(total_data, json_file)

    print(f"Total data points collected: {len(total_data)}")



