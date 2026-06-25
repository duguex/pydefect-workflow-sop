"""
Python implementation of read_openmx.c
Reads OpenMX scfout files and converts them to JSON format.
"""

import struct
import json
import numpy as np


class ScfOutReader:
    """Reader for OpenMX scfout binary files."""

    def __init__(self, filename):
        self.filename = filename
        self.conversion_switch = False
        self.version = 0
        self.SCFOUT_VERSION = 3
        self.LATEST_VERSION = 3

        # Global variables from read_scfout_YZ.h
        self.atomnum = 0
        self.Catomnum = 0
        self.Latomnum = 0
        self.Ratomnum = 0
        self.SpinP_switch = 0
        self.TCpyCell = 0
        self.Solver = 0
        self.ChemP = 0.0
        self.Valence_Electrons = 0
        self.Total_SpinS = 0.0
        self.E_Temp = 0.0
        self.order_max = 0

        # Arrays
        self.Total_NumOrbs = None
        self.FNAN = None
        self.natn = None
        self.ncn = None
        self.atv = None
        self.atv_ijk = None
        self.tv = None
        self.rtv = None
        self.Gxyz = None
        self.Hks = None
        self.iHks = None
        self.OLP = None
        self.DM = None
        self.iDM = None
        self.D_OLP = None
        self.OLP_L = None
        self.dipole_moment_core = np.zeros(4)
        self.dipole_moment_background = np.zeros(4)

    def _fread(self, f, fmt, count=1):
        """Read binary data with optional endianness conversion."""
        fmt_char = fmt[-1]
        size = struct.calcsize(fmt)

        if fmt_char == 'i':
            data = f.read(size * count)
            values = list(struct.unpack(f'<{count}i', data))

            if self.conversion_switch:
                for i in range(len(values)):
                    values[i] = self._swap_int32(values[i])

            return values
        elif fmt_char == 'd':
            data = f.read(size * count)
            values = list(struct.unpack(f'<{count}d', data))
            return values
        elif fmt_char == 'c':
            data = f.read(size * count)
            return data.decode('ascii', errors='ignore')
        else:
            raise ValueError(f"Unsupported format: {fmt}")

    def _swap_int32(self, value):
        """Swap bytes for 32-bit integer."""
        packed = struct.pack('<i', value)
        swapped = struct.unpack('>i', packed)[0]
        return swapped

    def read(self):
        """Read the scfout file."""
        with open(self.filename, 'rb') as f:
            self._read_header(f)
            self._read_translations(f)
            self._read_orbital_info(f)
            self._read_neighbor_info(f)
            self._read_cell_vectors(f)
            self._read_atomic_coordinates(f)
            self._allocate_matrices()
            self._read_hamiltonian(f)
            self._read_overlap(f)
            self._read_density_matrix(f)
            self._read_solver_and_chemical_potential(f)

    def _read_header(self, f):
        """Read header information: atomnum, SpinP_switch, version."""
        i_vec = self._fread(f, 'i', 6)

        # Check endianness
        spin_val = i_vec[1]
        if spin_val < 0 or spin_val > (self.LATEST_VERSION * 4 + 3):
            self.conversion_switch = True
            for i in range(6):
                i_vec[i] = self._swap_int32(i_vec[i])

            # Re-check after conversion
            spin_val = i_vec[1]
            if spin_val < 0 or spin_val > (self.LATEST_VERSION * 4 + 3):
                raise ValueError("Error: Mismatch of the endianness")

        self.atomnum = i_vec[0]
        self.SpinP_switch = i_vec[1] % 4
        self.version = i_vec[1] // 4

        if self.version != self.SCFOUT_VERSION:
            print(f"The file format of the SCFOUT file: {self.version}")
            print("The version is not supported by the current read_scfout")
            raise ValueError(f"Unsupported version: {self.version}")

        self.Catomnum = i_vec[2]
        self.Latomnum = i_vec[3]
        self.Ratomnum = i_vec[4]
        self.TCpyCell = i_vec[5]

    def _read_translations(self, f):
        """Read translation vectors."""
        # Read order_max
        i_vec = self._fread(f, 'i', 1)
        self.order_max = i_vec[0]

        # Read atv
        self.atv = np.zeros((self.TCpyCell + 1, 4))
        for Rn in range(self.TCpyCell + 1):
            d_vec = self._fread(f, 'd', 4)
            self.atv[Rn] = d_vec

        # Read atv_ijk
        self.atv_ijk = np.zeros((self.TCpyCell + 1, 4), dtype=int)
        for Rn in range(self.TCpyCell + 1):
            i_vec = self._fread(f, 'i', 4)
            self.atv_ijk[Rn] = i_vec

    def _read_orbital_info(self, f):
        """Read orbital information."""
        # Total_NumOrbs
        p_vec = self._fread(f, 'i', self.atomnum)
        self.Total_NumOrbs = np.zeros(self.atomnum + 1, dtype=int)
        self.Total_NumOrbs[0] = 1
        for ct_AN in range(1, self.atomnum + 1):
            self.Total_NumOrbs[ct_AN] = p_vec[ct_AN - 1]

        # FNAN
        p_vec = self._fread(f, 'i', self.atomnum)
        self.FNAN = np.zeros(self.atomnum + 1, dtype=int)
        self.FNAN[0] = 0
        for ct_AN in range(1, self.atomnum + 1):
            self.FNAN[ct_AN] = p_vec[ct_AN - 1]

    def _read_neighbor_info(self, f):
        """Read neighbor information."""
        # Allocate natn
        self.natn = []
        for ct_AN in range(self.atomnum + 1):
            self.natn.append(np.zeros(self.FNAN[ct_AN] + 1, dtype=int))

        # Read natn
        for ct_AN in range(1, self.atomnum + 1):
            i_vec = self._fread(f, 'i', self.FNAN[ct_AN] + 1)
            self.natn[ct_AN] = np.array(i_vec)

        # Allocate ncn
        self.ncn = []
        for ct_AN in range(self.atomnum + 1):
            self.ncn.append(np.zeros(self.FNAN[ct_AN] + 1, dtype=int))

        # Read ncn
        for ct_AN in range(1, self.atomnum + 1):
            i_vec = self._fread(f, 'i', self.FNAN[ct_AN] + 1)
            self.ncn[ct_AN] = np.array(i_vec)

    def _read_cell_vectors(self, f):
        """Read unit cell vectors."""
        self.tv = np.zeros((4, 4))
        self.rtv = np.zeros((4, 4))

        for i in range(1, 4):
            d_vec = self._fread(f, 'd', 4)
            self.tv[i] = d_vec

        for i in range(1, 4):
            d_vec = self._fread(f, 'd', 4)
            self.rtv[i] = d_vec

    def _read_atomic_coordinates(self, f):
        """Read atomic coordinates."""
        self.Gxyz = np.zeros((self.atomnum + 1, 60))
        for ct_AN in range(1, self.atomnum + 1):
            d_vec = self._fread(f, 'd', 4)
            self.Gxyz[ct_AN, :4] = d_vec

    def _allocate_matrices(self):
        """Allocate memory for matrices."""
        # Hks
        self.Hks = []
        for spin in range(self.SpinP_switch + 1):
            Hks_spin = []
            for ct_AN in range(self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                Hks_ct = []
                for h_AN in range(self.FNAN[ct_AN] + 1):
                    if ct_AN == 0:
                        TNO2 = 1
                    else:
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                    Hks_ct.append(np.zeros((TNO1, TNO2)))
                Hks_spin.append(Hks_ct)
            self.Hks.append(Hks_spin)

        # iHks (only for SpinP_switch == 3)
        if self.SpinP_switch == 3:
            self.iHks = []
            for spin in range(3):
                iHks_spin = []
                for ct_AN in range(self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    iHks_ct = []
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        if ct_AN == 0:
                            TNO2 = 1
                        else:
                            Gh_AN = self.natn[ct_AN][h_AN]
                            TNO2 = self.Total_NumOrbs[Gh_AN]
                        iHks_ct.append(np.zeros((TNO1, TNO2)))
                    iHks_spin.append(iHks_ct)
                self.iHks.append(iHks_spin)

        # OLP
        self.OLP = []
        for ct_AN in range(self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            OLP_ct = []
            for h_AN in range(self.FNAN[ct_AN] + 1):
                if ct_AN == 0:
                    TNO2 = 1
                else:
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]
                OLP_ct.append(np.zeros((TNO1, TNO2)))
            self.OLP.append(OLP_ct)

        # D_OLP
        self.D_OLP = []
        for ct_AN in range(self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            D_OLP_ct = []
            for h_AN in range(self.FNAN[ct_AN] + 1):
                if ct_AN == 0:
                    TNO2 = 1
                else:
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]
                D_OLP_h = []
                for i in range(TNO1):
                    D_OLP_h.append(np.zeros((TNO2, 3)))
                D_OLP_ct.append(D_OLP_h)
            self.D_OLP.append(D_OLP_ct)

        # OLP_L
        self.OLP_L = []
        for ct_AN in range(self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            OLP_L_ct = []
            for h_AN in range(self.FNAN[ct_AN] + 1):
                if ct_AN == 0:
                    TNO2 = 1
                else:
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]
                OLP_L_h = []
                for i in range(TNO1):
                    OLP_L_h.append(np.zeros((TNO2, 3)))
                OLP_L_ct.append(OLP_L_h)
            self.OLP_L.append(OLP_L_ct)

        # DM
        self.DM = []
        for spin in range(self.SpinP_switch + 1):
            DM_spin = []
            for ct_AN in range(self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                DM_ct = []
                for h_AN in range(self.FNAN[ct_AN] + 1):
                    if ct_AN == 0:
                        TNO2 = 1
                    else:
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                    DM_ct.append(np.zeros((TNO1, TNO2)))
                DM_spin.append(DM_ct)
            self.DM.append(DM_spin)

        # iDM
        self.iDM = []
        for spin in range(2):
            iDM_spin = []
            for ct_AN in range(self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                iDM_ct = []
                for h_AN in range(self.FNAN[ct_AN] + 1):
                    if ct_AN == 0:
                        TNO2 = 1
                    else:
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                    iDM_ct.append(np.zeros((TNO1, TNO2)))
                iDM_spin.append(iDM_ct)
            self.iDM.append(iDM_spin)

    def _read_hamiltonian(self, f):
        """Read Hamiltonian matrix."""
        for spin in range(self.SpinP_switch + 1):
            for ct_AN in range(1, self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                for h_AN in range(self.FNAN[ct_AN] + 1):
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]
                    for i in range(TNO1):
                        d_vec = self._fread(f, 'd', TNO2)
                        self.Hks[spin][ct_AN][h_AN][i] = d_vec

        # iHks (only for SpinP_switch == 3)
        if self.SpinP_switch == 3:
            for spin in range(3):
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                        for i in range(TNO1):
                            d_vec = self._fread(f, 'd', TNO2)
                            self.iHks[spin][ct_AN][h_AN][i] = d_vec

    def _read_overlap(self, f):
        """Read overlap matrix."""
        for ct_AN in range(1, self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            for h_AN in range(self.FNAN[ct_AN] + 1):
                Gh_AN = self.natn[ct_AN][h_AN]
                TNO2 = self.Total_NumOrbs[Gh_AN]
                for i in range(TNO1):
                    d_vec = self._fread(f, 'd', TNO2)
                    self.OLP[ct_AN][h_AN][i] = d_vec

        # D_OLP (check if file has this data)
        current_pos = f.tell()
        f.seek(0, 2)
        file_end = f.tell()
        f.seek(current_pos)
        
        if current_pos < file_end:
            try:
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                        for i in range(TNO1):
                            for j in range(TNO2):
                                d_vec = self._fread(f, 'd', 3)
                                self.D_OLP[ct_AN][h_AN][i][j] = d_vec
            except Exception as e:
                # D_OLP data may not exist in overlap.scfout
                pass

        # OLP_L (check if file has this data)
        current_pos = f.tell()
        f.seek(0, 2)
        file_end = f.tell()
        f.seek(current_pos)
        
        if current_pos < file_end:
            try:
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                        for i in range(TNO1):
                            for j in range(TNO2):
                                d_vec = self._fread(f, 'd', 3)
                                self.OLP_L[ct_AN][h_AN][i][j] = d_vec
            except Exception as e:
                # OLP_L data may not exist in overlap.scfout
                pass

    def _read_density_matrix(self, f):
        """Read density matrix."""
        # Check if we're at end of file (for overlap.scfout files without DM)
        current_pos = f.tell()
        f.seek(0, 2)  # Seek to end
        file_end = f.tell()
        f.seek(current_pos)  # Seek back

        if current_pos >= file_end:
            # No density matrix data (e.g., in overlap.scfout files)
            return

        try:
            for spin in range(self.SpinP_switch + 1):
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                        for i in range(TNO1):
                            d_vec = self._fread(f, 'd', TNO2)
                            self.DM[spin][ct_AN][h_AN][i] = d_vec

            for spin in range(2):
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]
                        for i in range(TNO1):
                            d_vec = self._fread(f, 'd', TNO2)
                            self.iDM[spin][ct_AN][h_AN][i] = d_vec
        except Exception as e:
            # If reading fails, we might be at the end of file (overlap.scfout doesn't have iDM)
            pass

    def _read_solver_and_chemical_potential(self, f):
        """Read solver and chemical potential."""
        # Check if we're at end of file (for overlap.scfout files without solver info)
        current_pos = f.tell()
        f.seek(0, 2)  # Seek to end
        file_end = f.tell()
        f.seek(current_pos)  # Seek back

        if current_pos >= file_end:
            # No solver/chemical potential data (e.g., in overlap.scfout files)
            self.Solver = 0
            self.ChemP = 0.0
            self.E_Temp = 0.0
            self.Valence_Electrons = 0
            self.Total_SpinS = 0.0
            return

        try:
            i_vec = self._fread(f, 'i', 1)
            self.Solver = i_vec[0]

            d_vec = self._fread(f, 'd', 10)
            self.ChemP = d_vec[0]
            self.E_Temp = d_vec[1]
            self.dipole_moment_core[1] = d_vec[2]
            self.dipole_moment_core[2] = d_vec[3]
            self.dipole_moment_core[3] = d_vec[4]
            self.dipole_moment_background[1] = d_vec[5]
            self.dipole_moment_background[2] = d_vec[6]
            self.dipole_moment_background[3] = d_vec[7]
            self.Valence_Electrons = d_vec[8]
            self.Total_SpinS = d_vec[9]
        except Exception as e:
            # If reading fails, we might be at the end of file (overlap.scfout doesn't have solver info)
            self.Solver = 0
            self.ChemP = 0.0
            self.E_Temp = 0.0
            self.Valence_Electrons = 0
            self.Total_SpinS = 0.0

    def to_dict(self):
        """Convert data to dictionary format (in-memory, no file I/O)."""
        # Check if any atoms have neighbors
        has_neighbors = any(self.FNAN[ct_AN] > 0 for ct_AN in range(1, self.atomnum + 1))

        result = {}

        # edge_index
        edge_index = [[], []]
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    edge_index[0].append(ct_AN - 1)
                    edge_index[1].append(self.natn[ct_AN][h_AN] - 1)
        result['edge_index'] = edge_index

        # pos
        pos = []
        for ct_AN in range(1, self.atomnum + 1):
            pos.append([float(f"{self.Gxyz[ct_AN][1]:.7f}"),
                       float(f"{self.Gxyz[ct_AN][2]:.7f}"),
                       float(f"{self.Gxyz[ct_AN][3]:.7f}")])
        result['pos'] = pos

        # cell_shift
        cell_shift = []
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    Rn = self.ncn[ct_AN][h_AN]
                    cell_shift.append([int(self.atv_ijk[Rn][1]),
                                       int(self.atv_ijk[Rn][2]),
                                       int(self.atv_ijk[Rn][3])])
        result['cell_shift'] = cell_shift

        # inv_edge_idx
        inv_edge_idx = []
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    Rn = self.ncn[ct_AN][h_AN]
                    src = ct_AN - 1
                    tar = self.natn[ct_AN][h_AN] - 1
                    shift = [int(self.atv_ijk[Rn][1]),
                            int(self.atv_ijk[Rn][2]),
                            int(self.atv_ijk[Rn][3])]

                    idx_tmp = 0
                    found = False
                    for ct_AN_tmp in range(1, self.atomnum + 1):
                        if self.FNAN[ct_AN_tmp] == 0:
                            continue
                        for h_AN_tmp in range(1, self.FNAN[ct_AN_tmp] + 1):
                            Rn_tmp = self.ncn[ct_AN_tmp][h_AN_tmp]
                            src_tmp = ct_AN_tmp - 1
                            tar_tmp = self.natn[ct_AN_tmp][h_AN_tmp] - 1
                            shift_tmp = [int(self.atv_ijk[Rn_tmp][1]),
                                        int(self.atv_ijk[Rn_tmp][2]),
                                        int(self.atv_ijk[Rn_tmp][3])]

                            if (src_tmp != tar or tar_tmp != src or
                                shift_tmp[0] + shift[0] != 0 or
                                shift_tmp[1] + shift[1] != 0 or
                                shift_tmp[2] + shift[2] != 0):
                                idx_tmp += 1
                                continue

                            inv_edge_idx.append(idx_tmp)
                            found = True
                            break
                        if found:
                            break
        result['inv_edge_idx'] = inv_edge_idx

        # nbr_shift
        nbr_shift = []
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    Rn = self.ncn[ct_AN][h_AN]
                    nbr_shift.append([float(f"{self.atv[Rn][1]:.7f}"),
                                     float(f"{self.atv[Rn][2]:.7f}"),
                                     float(f"{self.atv[Rn][3]:.7f}")])
        result['nbr_shift'] = nbr_shift

        # Hon (on-site Hamiltonian) - flatten to match C format
        Hon = []
        for spin in range(self.SpinP_switch + 1):
            Hon_spin = []
            for ct_AN in range(1, self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                h_AN = 0  # on-site only
                Gh_AN = self.natn[ct_AN][h_AN]
                TNO2 = self.Total_NumOrbs[Gh_AN]

                # Flatten the matrix to match C format
                flat_mat = []
                for i in range(TNO1):
                    for j in range(TNO2):
                        val = float(f"{self.Hks[spin][ct_AN][h_AN][i][j]:.10f}")
                        flat_mat.append(val)
                Hon_spin.append(flat_mat)
            Hon.append(Hon_spin)
        result['Hon'] = Hon

        # Hoff (off-site Hamiltonian) - flatten to match C format
        Hoff = []
        for spin in range(self.SpinP_switch + 1):
            Hoff_spin = []
            if has_neighbors:
                for ct_AN in range(1, self.atomnum + 1):
                    if self.FNAN[ct_AN] == 0:
                        continue
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(1, self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]

                        # Flatten the matrix to match C format
                        flat_mat = []
                        for i in range(TNO1):
                            for j in range(TNO2):
                                val = float(f"{self.Hks[spin][ct_AN][h_AN][i][j]:.10f}")
                                flat_mat.append(val)
                        Hoff_spin.append(flat_mat)
            Hoff.append(Hoff_spin)
        result['Hoff'] = Hoff

        # iHon and iHoff (only for SpinP_switch == 3) - flatten to match C format
        if self.SpinP_switch == 3:
            iHon = []
            for spin in range(3):
                iHon_spin = []
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    h_AN = 0  # on-site only
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]

                    # Flatten the matrix to match C format
                    flat_mat = []
                    for i in range(TNO1):
                        for j in range(TNO2):
                            val = float(f"{self.iHks[spin][ct_AN][h_AN][i][j]:.10f}")
                            flat_mat.append(val)
                    iHon_spin.append(flat_mat)
                iHon.append(iHon_spin)
            result['iHon'] = iHon

            iHoff = []
            for spin in range(3):
                iHoff_spin = []
                if has_neighbors:
                    for ct_AN in range(1, self.atomnum + 1):
                        if self.FNAN[ct_AN] == 0:
                            continue
                        TNO1 = self.Total_NumOrbs[ct_AN]
                        for h_AN in range(1, self.FNAN[ct_AN] + 1):
                            Gh_AN = self.natn[ct_AN][h_AN]
                            TNO2 = self.Total_NumOrbs[Gh_AN]

                            # Flatten the matrix to match C format
                            flat_mat = []
                            for i in range(TNO1):
                                for j in range(TNO2):
                                    val = float(f"{self.iHks[spin][ct_AN][h_AN][i][j]:.10f}")
                                    flat_mat.append(val)
                            iHoff_spin.append(flat_mat)
                iHoff.append(iHoff_spin)
            result['iHoff'] = iHoff

        # Son (on-site overlap) - flatten to match C format
        Son = []
        for ct_AN in range(1, self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            h_AN = 0  # on-site only
            Gh_AN = self.natn[ct_AN][h_AN]
            TNO2 = self.Total_NumOrbs[Gh_AN]

            # Flatten the matrix to match C format
            flat_mat = []
            for i in range(TNO1):
                for j in range(TNO2):
                    val = float(f"{self.OLP[ct_AN][h_AN][i][j]:.10f}")
                    flat_mat.append(val)
            Son.append(flat_mat)
        result['Son'] = Son

        # Soff (off-site overlap) - flatten to match C format
        Soff = []
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                TNO1 = self.Total_NumOrbs[ct_AN]
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]

                    # Flatten the matrix to match C format
                    flat_mat = []
                    for i in range(TNO1):
                        for j in range(TNO2):
                            val = float(f"{self.OLP[ct_AN][h_AN][i][j]:.10f}")
                            flat_mat.append(val)
                    Soff.append(flat_mat)
        result['Soff'] = Soff

        # Lon (on-site OLP_L) - flatten to match C format
        Lon = []
        for ct_AN in range(1, self.atomnum + 1):
            TNO1 = self.Total_NumOrbs[ct_AN]
            h_AN = 0  # on-site only
            Gh_AN = self.natn[ct_AN][h_AN]
            TNO2 = self.Total_NumOrbs[Gh_AN]

            # Flatten the matrix to match C format
            flat_mat = []
            for i in range(TNO1):
                for j in range(TNO2):
                    flat_mat.append([
                        float(f"{self.OLP_L[ct_AN][h_AN][i][j][0]:.7f}"),
                        float(f"{self.OLP_L[ct_AN][h_AN][i][j][1]:.7f}"),
                        float(f"{self.OLP_L[ct_AN][h_AN][i][j][2]:.7f}")
                    ])
            Lon.append(flat_mat)
        result['Lon'] = Lon

        # Loff (off-site OLP_L) - flatten to match C format
        Loff = []
        if has_neighbors:
            for ct_AN in range(1, self.atomnum + 1):
                if self.FNAN[ct_AN] == 0:
                    continue
                TNO1 = self.Total_NumOrbs[ct_AN]
                for h_AN in range(1, self.FNAN[ct_AN] + 1):
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]

                    # Flatten the matrix to match C format
                    flat_mat = []
                    for i in range(TNO1):
                        for j in range(TNO2):
                            flat_mat.append([
                                float(f"{self.OLP_L[ct_AN][h_AN][i][j][0]:.7f}"),
                                float(f"{self.OLP_L[ct_AN][h_AN][i][j][1]:.7f}"),
                                float(f"{self.OLP_L[ct_AN][h_AN][i][j][2]:.7f}")
                            ])
                    Loff.append(flat_mat)
        result['Loff'] = Loff

        # Don (on-site density matrix) - flatten to match C format
        Don = []
        for spin in range(self.SpinP_switch + 1):
            Don_spin = []
            for ct_AN in range(1, self.atomnum + 1):
                TNO1 = self.Total_NumOrbs[ct_AN]
                h_AN = 0  # on-site only
                Gh_AN = self.natn[ct_AN][h_AN]
                TNO2 = self.Total_NumOrbs[Gh_AN]

                # Flatten the matrix to match C format
                flat_mat = []
                for i in range(TNO1):
                    for j in range(TNO2):
                        val = float(f"{self.DM[spin][ct_AN][h_AN][i][j]:.10f}")
                        flat_mat.append(val)
                Don_spin.append(flat_mat)
            Don.append(Don_spin)
        result['Don'] = Don

        # Doff (off-site density matrix) - flatten to match C format
        Doff = []
        for spin in range(self.SpinP_switch + 1):
            Doff_spin = []
            if has_neighbors:
                for ct_AN in range(1, self.atomnum + 1):
                    if self.FNAN[ct_AN] == 0:
                        continue
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    for h_AN in range(1, self.FNAN[ct_AN] + 1):
                        Gh_AN = self.natn[ct_AN][h_AN]
                        TNO2 = self.Total_NumOrbs[Gh_AN]

                        # Flatten the matrix to match C format
                        flat_mat = []
                        for i in range(TNO1):
                            for j in range(TNO2):
                                val = float(f"{self.DM[spin][ct_AN][h_AN][i][j]:.10f}")
                                flat_mat.append(val)
                        Doff_spin.append(flat_mat)
            Doff.append(Doff_spin)
        result['Doff'] = Doff

        # iDon and iDoff (only for SpinP_switch == 3) - flatten to match C format
        if self.SpinP_switch == 3:
            iDon = []
            for spin in range(2):
                iDon_spin = []
                for ct_AN in range(1, self.atomnum + 1):
                    TNO1 = self.Total_NumOrbs[ct_AN]
                    h_AN = 0  # on-site only
                    Gh_AN = self.natn[ct_AN][h_AN]
                    TNO2 = self.Total_NumOrbs[Gh_AN]

                    # Flatten the matrix to match C format
                    flat_mat = []
                    for i in range(TNO1):
                        for j in range(TNO2):
                            val = float(f"{self.iDM[spin][ct_AN][h_AN][i][j]:.10f}")
                            flat_mat.append(val)
                    iDon_spin.append(flat_mat)
                iDon.append(iDon_spin)
            result['iDon'] = iDon

            iDoff = []
            for spin in range(2):
                iDoff_spin = []
                if has_neighbors:
                    for ct_AN in range(1, self.atomnum + 1):
                        if self.FNAN[ct_AN] == 0:
                            continue
                        TNO1 = self.Total_NumOrbs[ct_AN]
                        for h_AN in range(1, self.FNAN[ct_AN] + 1):
                            Gh_AN = self.natn[ct_AN][h_AN]
                            TNO2 = self.Total_NumOrbs[Gh_AN]

                            # Flatten the matrix to match C format
                            flat_mat = []
                            for i in range(TNO1):
                                for j in range(TNO2):
                                    val = float(f"{self.iDM[spin][ct_AN][h_AN][i][j]:.10f}")
                                    flat_mat.append(val)
                            iDoff_spin.append(flat_mat)
                iDoff.append(iDoff_spin)
            result['iDoff'] = iDoff

        return result

    def to_json(self, output_file='HS.json'):
        """Convert data to JSON format and write to file."""
        result = self.to_dict()

        def convert_numpy(obj):
            if isinstance(obj, (np.integer, np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2, default=convert_numpy)


def read_openmx(filename, output_file='HS.json'):
    """
    Read OpenMX scfout file and convert to JSON.

    Parameters:
    -----------
    filename : str
        Path to the scfout file
    output_file : str, optional
        Output JSON file name (default: 'HS.json')

    Returns:
    --------
    ScfOutReader
        Reader object containing all the data
    """
    reader = ScfOutReader(filename)
    reader.read()
    reader.to_json(output_file)
    return reader


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python read_openmx_py.py <scfout_file> [output_file]")
        sys.exit(1)

    scfout_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'HS.json'

    try:
        reader = read_openmx(scfout_file, output_file)
        print(f"Successfully converted {scfout_file} to {output_file}")
        print(f"Atoms: {reader.atomnum}")
        print(f"Spin: {reader.SpinP_switch}")
        print(f"Valence electrons: {reader.Valence_Electrons}")
        print(f"Chemical potential: {reader.ChemP:.6f}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)