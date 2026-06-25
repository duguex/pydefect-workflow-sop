import json
from tqdm import tqdm
import os
import numpy as np

class Poscar:
    def __init__(self, poscarPath):
        """
        initialize the Poscar-class by reading a POSCAR file
        """
        self.read(poscarPath)

    def read(self, poscarPath):
        """
        read the POSCAR file

        Ca4 O4                   # comment
        1.0                      # scaling
        9.678532 0.0 0.0         # lattice vector 1
        0.0 9.678532 0.0         # lattice vector 2
        0.0 0.0 9.678532         # lattice vector 3
        Ca                       # element list
        1                        # element number list
        Direct# or Cartesian     # coordinate type
        0.5 0.5 0.5              # coordinate list
        ...

        :param poscarPath:
        :return:
        """
        assert type(poscarPath) == str and os.path.isfile(poscarPath), "The parameter should be path of POSCAR-class file."

        # read POSCAR file
        with open(poscarPath, "r") as poscar:
            # read comment and scaling
            self.comment = poscar.readline().strip()
            scaling = float(poscar.readline().strip())

            # read lattice
            lattice = []
            for _ in range(3):
                lattice.append(poscar.readline().split())
            lattice = np.array(lattice)
            self.lattice = lattice.reshape(3, 3).astype(float) * scaling
            self.reciprocal = np.linalg.inv(self.lattice)

            # read element and number
            self.element = [element.replace("/","") for element in poscar.readline().split()]
            self.number = list(np.array(poscar.readline().split()).astype(int))

            # read selective dynamics and type = Direct or Cartesian
            coordinate = poscar.readline().strip()[0].upper()
            assert (
                coordinate in "CD"
            ), "the coordinate type should be Direct or Cartesian."
            self.isDirect = coordinate == "D"

            # read atomic position and additional information
            position = []
            addition = []

            while True:
                line = poscar.readline().split()
                if line:
                    position.append(line[:3])
                    addition.append(" ".join(line[3:]))
                else:
                    break

            self.position = np.array(position).astype(float)
            self.addition = addition

            # read density
            density = []
            while True:
                line = poscar.readline().split()
                if line:
                    density += line
                else:
                    break
            
        self.haveDensity = False
        if len(density) > 3:
            try:
                shape_of_density = np.array(density[:3]).astype(int)
                density = density[3:]
                assert len(density) == np.prod(shape_of_density), "the density data is not complete."
                self.haveDensity = True
            except:
                pass
        

        if self.haveDensity:
            density = np.array(density).astype(float)
            # reshape density
            # discrete data in order of x,0,0 x,y,0 x,y,z, therefore the index of data is z,y,x
            density = density.reshape(shape_of_density[::-1])
            # reverse z,y,x to x,y,z
            density = np.swapaxes(density, 0, 2)
            self.density=density

        self.name_and_label()

    def name_and_label(self):
        # label for every atom (element of the atom, index of the atom in the element list)
        element_array = np.repeat(self.element, self.number)
        index_array = sum([list(range(_number)) for _number in self.number], [])
        assert element_array.shape[0] == len(
            index_array
        ), "the shape of element_array and index_array should be the same."
        self.label = list(zip(element_array, index_array))

        # name the poscar by chemical formula
        self.name = "".join(
            [
                _element + ("" if _number == 1 else str(_number))
                for _element, _number in zip(self.element, self.number)
            ]
        )
        if self.name not in self.comment:
            self.comment += " " + self.name

def read_zfs_from_vasp(outcar_path):
    with open(outcar_path, "r") as file:
        text = file.read()

    lines = text.splitlines()
    tensor_start = lines.index(" Spin-spin contribution to zero-field splitting tensor (MHz)") + 4
    D_tensor_line = lines[tensor_start].strip()
    D_tensor = [float(value) for value in D_tensor_line.split()]

    eigenvalues = []
    eigenvectors = []
    eigen_start = lines.index(" after diagonalization", tensor_start) + 4
    for line in lines[eigen_start : eigen_start + 3]:
        parts = line.strip().split()
        eigenvalues.append(float(parts[0]))
        eigenvectors.append([float(parts[1]), float(parts[2]), float(parts[3])])

    data = {"D_tensor": D_tensor, "eigenvalues": eigenvalues, "eigenvectors": eigenvectors}

    return data

def read_energy(outcar_path):
    with open(outcar_path, "r") as file:
        text = file.read()

    lines = text.splitlines()
    for line in lines[::-1]:
        if "free  energy   TOTEN  =" in line:
            parts = line.split()
            return float(parts[-2])

def tom(ar):
    return [[ar[0], ar[3], ar[4]], 
            [ar[3], ar[1], ar[5]], 
            [ar[4], ar[5], ar[2]]]

total_data = {}

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
    if i == "read_zfs.py" or i == "data.json":
        continue

    try:
        energy = read_energy(f"{i}/OUTCAR")
    except:
        energy = None
    try:
        zfs_from_outcar = read_zfs_from_vasp(f"{i}/OUTCAR")
        zfs_from_outcar["D_tensor"] = tom(zfs_from_outcar["D_tensor"])
    except:
        # print(f"Error in read zfs result from OUTCAR in {i}.")
        zfs_from_outcar = None
    
    if json_count != 0:
        try:
            zfs_from_json = json.load(open(f"{i}/zfs.json"))
        except:
            print(f"Error in read zfs result from zfs.json in {i}.")
            zfs_from_json = None
        
        if zfs_from_outcar is not None or zfs_from_json is not None:
            total_data[i] = {"zfs_from_outcar": zfs_from_outcar, "zfs_from_json": zfs_from_json, "poscar": Poscar(f"{i}/POSCAR").position.tolist(), "energy": energy}
    elif zfs_from_outcar is not None:
        total_data[i] = {"zfs_from_outcar": zfs_from_outcar, "poscar": Poscar(f"{i}/POSCAR").position.tolist(), "energy": energy}

with open("data.json", "w") as json_file:
    json.dump(total_data, json_file)
