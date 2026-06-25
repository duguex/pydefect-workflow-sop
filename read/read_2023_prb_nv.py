import numpy as np
import json
import os


def read_yaml(yaml_path, json_path):
    # mass 是 number of atom 维向量
    # freq 是 3 number of atom 维向量
    # mode 和 modewom 都是 3 number of atom × number of atom 维矩阵

    with open(yaml_path, "r") as f:
        mass = []
        while True:
            line = f.readline()
            if "mass:" in line:
                mass.append(float(line.strip().split(" ")[-1]))
            elif line == "\n":
                break

        while True:
            line = f.readline()
            if "band:" in line:
                break

        freq = []
        mode = []
        number_of_atom = len(mass)
        while True:
            line = f.readline()
            if line == "\n":
                break
            else:
                freq.append(float(f.readline().strip().split()[1]) * 4.135669057 / 1e3)  # THz to eV
                f.readline()
                mode.append([])
                for atom in range(number_of_atom):
                    f.readline()
                    mode[-1].append(
                        [float(f.readline().replace(",", "").strip().split()[2]) for _ in range(3)]
                    )

    # 非质量加权的振动. 只有比例有意义, 值没有意义
    # modewom = [[vib_per_mode_per_atom / np.sqrt(mass_per_atom) for mass_per_atom, vib_per_mode_per_atom in
    #             zip(mass, vib_per_mode)] for vib_per_mode in mode]

    json.dump({"mass": mass, "freq": freq, "mode": mode}, open(json_path, "w"), indent=4)
    return None


def read_outcar(outcar_path, atom_list, vector=np.array([-1, -1, -1]), multi=100):
    # atom_list picks representive atoms and speed up further calculation process

    f = open(outcar_path, "r")
    lines = f.readlines()
    f.close()

    outcar = {"path": outcar_path,
              "energy": None,
              "length_of_vectors": None,
              "quadrupole_moment": None,
              "fermi_contact": None,
              "hyperfine": None}

    for line_index in range(len(lines))[::-1]:
        if all(outcar.values()):
            break

        if not outcar["energy"] and "free  energy   TOTEN  =" in lines[line_index]:
            outcar["energy"] = float(lines[line_index].split(" ")[-2])

        # length of vectors for diamond
        if not outcar["length_of_vectors"] and "length of vectors" in lines[line_index]:
            outcar["length_of_vectors"] = float([i for i in lines[line_index + 1].split(" ") if i][0])

        # nuclear quadrupole moment for 14N
        if not outcar["quadrupole_moment"] and "ion       Cq(MHz)       eta       Q (mb)" in lines[line_index]:
            outcar["quadrupole_moment"] = 4 / 5 * float([i for i in lines[line_index + 2].split(" ") if i][1])

        # dipolar
        if not outcar["hyperfine"] and "Dipolar hyperfine coupling parameters (MHz)" in lines[line_index]:
            outcar["hyperfine"] = {
                atom: [float(lines[line_index + 3 + atom][6 + i * 10:16 + i * 10]) for i in range(6)] for atom in
                atom_list}

        # hyperfine
        if not outcar["fermi_contact"] and outcar["hyperfine"] \
                and "Fermi contact (isotropic) hyperfine coupling parameter (MHz)" in lines[line_index]:
            # Fermi contact
            outcar["fermi_contact"] = {atom: float(lines[line_index + 3 + atom][-10:]) for atom in atom_list}
            for atom in atom_list:
                total_contribution = np.array(
                    [[outcar["hyperfine"][atom][0], outcar["hyperfine"][atom][3], outcar["hyperfine"][atom][4]],
                     [outcar["hyperfine"][atom][3], outcar["hyperfine"][atom][1], outcar["hyperfine"][atom][5]],
                     [outcar["hyperfine"][atom][4], outcar["hyperfine"][atom][5],
                      outcar["hyperfine"][atom][2]]]) + np.diag([outcar["fermi_contact"][atom] for _ in range(3)])

                vector = vector / np.linalg.norm(vector)
                outcar["hyperfine"][atom] = np.linalg.norm(vector.dot(total_contribution)) / multi * np.sign(
                    total_contribution[0][0])
                outcar["fermi_contact"][atom] /= multi

    return outcar


if __name__ == "__main__":
    # read vib mode
    # read_yaml("band.yaml", "mode.json")
    # read_yaml(r"e:\Desktop\10.14\band.yaml", r"e:\Desktop\10.14\mode.json")

    # read OUTCAR vib
    # json.dump(
    #     [read_outcar(directory + "/OUTCAR", [1, 2, 153, 27, 232, 231], vector=np.array([-1, -1, -1]), multi=100) for
    #      directory in os.listdir() if os.path.isdir(directory) and directory[0] in ["d", "p"] and "_" in directory],
    #     open("vib.json", "w"), indent=4)

    # read OUTCAR expand
    # json.dump(
    #     [read_outcar(directory + "/OUTCAR", [1, 2, 153, 27, 232, 231], vector=np.array([-1, -1, -1]), multi=100) for
    #      directory in os.listdir() if os.path.isdir(directory) and directory[0] == "s"], open("expand.json", "w"),
    #     indent=4)
    for i in ['2cross_0.12731200789802272',
              '2sheer',
              'a100',
              'a110',
              'a111',
              'cross_x+xy',
              'cross_x-xy',
              'cross_z+xy',
              'cross_z-xy']:
        json.dump(
            [read_outcar(i + "/" + directory + "/OUTCAR", [1, 2, 92, 32, 57, 55], vector=np.array([-1, -1, -1]),
                         multi=100) for
             directory in os.listdir(i) if os.path.isdir(i + "/" + directory) and directory[0] == "s"],
            open(f"{i}.json", "w"),
            indent=4)
