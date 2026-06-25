#!/usr/bin/env python3
"""
Utility to read OpenMX restart files (.rst format)

Based on RestartFileDFT.c from OpenMX source code
"""

import struct
import numpy as np
from typing import Dict, Tuple, List
import os


class RestartFileReader:
    """Read OpenMX restart files (.rst)"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = {}

    def read(self) -> Dict:
        """Read restart file and return data dictionary"""
        with open(self.filepath, 'rb') as f:
            # Read header (10 integers)
            header = struct.unpack('10i', f.read(40))
            self.data['SpinP_switch'] = header[0]  # 0: non spin polarized, 1: spin polarized, 3: non-collinear
            self.data['List_YOUSO_23'] = header[1]
            self.data['List_YOUSO_1'] = header[2]  # atomnum
            self.data['List_YOUSO_8'] = header[3]  # max # of atoms in rcut-off cluster
            self.data['List_YOUSO_7'] = header[4]  # max # of orbitals including an atom
            self.data['atomnum'] = header[5]
            self.data['wan1'] = header[6]  # species index
            self.data['TNO1'] = header[7]  # total number of orbitals for this species
            self.data['FNAN'] = header[8]  # number of neighboring atoms
            self.data['SO_switch'] = header[9]  # spin-orbit coupling

            # Read orbital information for each neighbor
            fnan = self.data['FNAN']
            num_orbital_blocks = (fnan + 1) * 6
            orbital_info = struct.unpack(f'{num_orbital_blocks}i', f.read(num_orbital_blocks * 4))

            # Parse orbital information (interleaved format)
            # Format: all Gh_AN, then all atv_ijk_x, then all atv_ijk_y, etc.
            num_neighbors = fnan + 1
            self.data['Gh_AN'] = list(orbital_info[0:num_neighbors])
            self.data['atv_ijk_x'] = list(orbital_info[num_neighbors:2*num_neighbors])
            self.data['atv_ijk_y'] = list(orbital_info[2*num_neighbors:3*num_neighbors])
            self.data['atv_ijk_z'] = list(orbital_info[3*num_neighbors:4*num_neighbors])
            self.data['wan2'] = list(orbital_info[4*num_neighbors:5*num_neighbors])
            self.data['TNO2'] = list(orbital_info[5*num_neighbors:6*num_neighbors])

            # Read Uele (electronic energy)
            self.data['Uele'] = struct.unpack('d', f.read(8))[0]

            # Read Kohn-Sham Hamiltonian
            spin_polarized = self.data['SpinP_switch']
            tno1 = self.data['TNO1']

            H = []
            for spin in range(spin_polarized + 1):
                H_spin = []
                for h_an in range(fnan + 1):
                    tno2 = self.data['TNO2'][h_an]
                    # Read TNO1 x TNO2 matrix
                    h_block = np.array(struct.unpack(f'{tno1 * tno2}d', f.read(tno1 * tno2 * 8)))
                    H_spin.append(h_block.reshape(tno1, tno2))
                H.append(H_spin)
            self.data['H'] = H

            # Read non-collinear SOC Hamiltonian if needed
            if spin_polarized == 3:
                iHNL = []
                for spin in range(spin_polarized):  # spin = 0,1,2 (3 channels for SpinP_switch=3)
                    iH_spin = []
                    for h_an in range(fnan + 1):
                        tno2 = self.data['TNO2'][h_an]
                        h_block = np.array(struct.unpack(f'{tno1 * tno2}d', f.read(tno1 * tno2 * 8)))
                        iH_spin.append(h_block.reshape(tno1, tno2))
                    iHNL.append(iH_spin)
                self.data['iHNL'] = iHNL

            # Read Density Matrix (DM)
            DM = []
            for spin in range(spin_polarized + 1):
                DM_spin = []
                for h_an in range(fnan + 1):
                    tno2 = self.data['TNO2'][h_an]
                    dm_block = np.array(struct.unpack(f'{tno1 * tno2}d', f.read(tno1 * tno2 * 8)))
                    DM_spin.append(dm_block.reshape(tno1, tno2))
                DM.append(DM_spin)
            self.data['DM'] = DM

            # NOTE: iDM is NOT stored in RST files!
            # RST files only store:
            # - H (Hamiltonian): spin 0,1,2,3 (4 channels)
            # - iHNL (imaginary non-local Hamiltonian): spin 0,1,2 (3 channels for SOC)
            # - DM[0] (density matrix real part): spin 0,1,2,3 (4 channels)
            # iDM (imaginary density matrix) is NOT written to RST files
            # iDM is only used in memory during SCF calculations

        return self.data

    def print_info(self):
        """Print information about the restart file"""
        print(f"\n=== Restart File: {self.filepath} ===")
        print(f"SpinP_switch: {self.data['SpinP_switch']} (0: non-spin, 1: spin, 3: non-collinear)")
        print(f"Number of atoms: {self.data['atomnum']}")
        print(f"Species index (wan1): {self.data['wan1']}")
        print(f"Total orbitals (TNO1): {self.data['TNO1']}")
        print(f"Number of neighbors (FNAN): {self.data['FNAN']}")
        print(f"SO_switch: {self.data['SO_switch']}")
        print(f"Uele: {self.data['Uele']:.6f} Hartree")
        print(f"\nHamiltonian matrix shape per spin/neighbor:")
        for spin, H_spin in enumerate(self.data['H']):
            print(f"  Spin {spin}: {len(H_spin)} neighbors")
            for h_an, h_block in enumerate(H_spin):
                print(f"    Neighbor {h_an}: {h_block.shape}")

        # Print iHNL information for SOC systems
        if 'iHNL' in self.data:
            print(f"\nImaginary Hamiltonian (iHNL) shape per spin/neighbor:")
            for spin, iHNL_spin in enumerate(self.data['iHNL']):
                print(f"  Spin {spin}: {len(iHNL_spin)} neighbors")
                for h_an, ihnl_block in enumerate(iHNL_spin):
                    print(f"    Neighbor {h_an}: {ihnl_block.shape} (range: [{ihnl_block.min():.3e}, {ihnl_block.max():.3e}])")

        print(f"\nDensity matrix (DM[0]) shape per spin/neighbor:")
        for spin, DM_spin in enumerate(self.data['DM']):
            print(f"  Spin {spin}: {len(DM_spin)} neighbors")
            for h_an, dm_block in enumerate(DM_spin):
                print(f"    Neighbor {h_an}: {dm_block.shape} (range: [{dm_block.min():.3e}, {dm_block.max():.3e}])")

        # SOC DM channels summary
        if self.data['SpinP_switch'] == 3:
            print(f"\nSOC data channels summary (stored in RST):")
            print(f"  H[0] = Re(αα) - spin up Hamiltonian (real)")
            print(f"  H[1] = Re(ββ) - spin down Hamiltonian (real)")
            print(f"  H[2] = Re(αβ) - spin cross Hamiltonian (real)")
            print(f"  H[3] = Im(αβ) - spin cross Hamiltonian (imaginary)")
            print(f"\n  iHNL[0] = Im(αα) - spin up Hamiltonian (imaginary)")
            print(f"  iHNL[1] = Im(ββ) - spin down Hamiltonian (imaginary)")
            print(f"  iHNL[2] = Im(αβ) - spin cross Hamiltonian (imaginary)")
            print(f"\n  DM[0][0] = Re(αα) - spin up density (real)")
            print(f"  DM[0][1] = Re(ββ) - spin down density (real)")
            print(f"  DM[0][2] = Re(αβ) - spin cross density (real)")
            print(f"  DM[0][3] = Im(αβ) - spin cross density (imaginary)")
            print(f"\n  Total: 11 scalar channels (4 H + 3 iHNL + 4 DM)")
            print(f"\n  NOTE: iDM (Im(αα), Im(ββ)) is NOT stored in RST files!")
            print(f"  iDM is only used in memory during SCF calculations.")


def read_restart_directory(rst_dir: str, system_name: str) -> Dict[int, Dict]:
    """Read all restart files in a directory"""
    restart_files = {}
    
    # Find all .rst files
    for atom_idx in range(1, 1000):  # Reasonable upper limit
        rst_file = os.path.join(rst_dir, f"{system_name}.rst{atom_idx}")
        if os.path.exists(rst_file):
            reader = RestartFileReader(rst_file)
            data = reader.read()
            restart_files[atom_idx] = data
            print(f"Read {rst_file}")
        else:
            break
    
    return restart_files


class CrstFileReader:
    """Read OpenMX charge density restart files (.crst)"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = {}

    def read(self) -> Dict:
        """Read .crst file and return data dictionary
        
        .crst file format:
        - Binary file
        - Contains My_NumGridB_AB double values
        - Data = Density_Grid_B[spin] - ADensity_Grid_B
        """
        with open(self.filepath, 'rb') as f:
            # Read all data as doubles
            data_bytes = f.read()
            num_doubles = len(data_bytes) // 8
            density_diff = struct.unpack(f'{num_doubles}d', data_bytes)
            
            self.data['density_diff'] = np.array(density_diff)
            self.data['num_grid_points'] = num_doubles
        
        return self.data

    def print_info(self):
        """Print information about the .crst file"""
        print(f"\n=== Crst File: {self.filepath} ===")
        print(f"Number of grid points: {self.data['num_grid_points']}")
        print(f"Data range: [{self.data['density_diff'].min():.6e}, {self.data['density_diff'].max():.6e}]")
        print(f"Data mean: {self.data['density_diff'].mean():.6e}")
        print(f"Data std: {self.data['density_diff'].std():.6e}")


def read_crst_directory(rst_dir: str, system_name: str, spin: int = 0, 
                        numprocs: int = 64, history: int = 1) -> Dict[int, Dict]:
    """Read all .crst files in a directory
    
    Args:
        rst_dir: Directory containing restart files
        system_name: System name (e.g., "openmx")
        spin: Spin channel (0 for spin up, 1 for spin down)
        numprocs: Number of MPI processes
        history: History index (0 for current, 1 for previous, etc.)
    
    Returns:
        Dictionary mapping processor ID to .crst data
    """
    crst_files = {}
    
    for proc_id in range(numprocs):
        crst_file = os.path.join(rst_dir, f"{system_name}.crst{spin}_{proc_id}_{history}")
        if os.path.exists(crst_file):
            reader = CrstFileReader(crst_file)
            data = reader.read()
            crst_files[proc_id] = data
            print(f"Read {crst_file}")
        else:
            break
    
    return crst_files


class CrstCheckReader:
    """Read OpenMX charge density restart check file (.crst_check)"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = {}

    def read(self) -> Dict:
        """Read .crst_check file and return data dictionary
        
        .crst_check file format:
        - Text file, one line
        - Contains: numprocs Ngrid1 Ngrid2 Ngrid3 SpinP_switch
        """
        with open(self.filepath, 'r') as f:
            line = f.read().strip()
            values = list(map(int, line.split()))
            
            self.data['numprocs'] = values[0]
            self.data['Ngrid1'] = values[1]
            self.data['Ngrid2'] = values[2]
            self.data['Ngrid3'] = values[3]
            self.data['SpinP_switch'] = values[4]
        
        return self.data

    def print_info(self):
        """Print information about the .crst_check file"""
        print(f"\n=== Crst Check File: {self.filepath} ===")
        print(f"Number of processors: {self.data['numprocs']}")
        print(f"Grid size: {self.data['Ngrid1']} x {self.data['Ngrid2']} x {self.data['Ngrid3']}")
        print(f"Spin polarization: {self.data['SpinP_switch']} (0: non-spin, 1: spin, 3: non-collinear)")


def verify_dm_reconstruction(filepath: str):
    """
    Verify that DM can be reconstructed from H + ChemP.

    This is a conceptual verification. Full implementation would require:
    1. Assembling full H matrix from sparse format
    2. Diagonalizing H using LAPACK
    3. Computing occupation numbers
    4. Reconstructing DM
    """
    reader = RestartFileReader(filepath)
    data = reader.read()

    print("\n" + "="*70)
    print("DM RECONSTRUCTION VERIFICATION")
    print("="*70)

    print(f"\nFile: {filepath}")
    print(f"Uele: {data['Uele']:.10f} Hartree")
    if 'ChemP' in data:
        print(f"ChemP (stored): {data['ChemP']:.10f} Hartree ({data['ChemP'] * 27.2114:.4f} eV)")
    else:
        print("ChemP: Not available in this restart file")
        return

    print(f"\nSpinP_switch: {data['SpinP_switch']}")
    print(f"Total orbitals (TNO1): {data['TNO1']}")
    print(f"Number of neighbors (FNAN): {data['FNAN']}")

    print("\n" + "-"*70)
    print("CONCEPTUAL DM RECONSTRUCTION ALGORITHM:")
    print("-"*70)
    print("""
1. Assemble full Hamiltonian matrix H from sparse format
   - Combine all neighbor blocks into single matrix
   - Matrix size: TNO_total x TNO_total

2. Diagonalize H to get eigenvalues and eigenvectors
   - Solve: H * C = ε * C
   - Use: EigenBand_lapack() or Eigen_PHH()
   - Get: eigenvalues ε_n, eigenvectors C_{μn}

3. Calculate Fermi level (ChemP)
   - Use binary search to satisfy: Σ f(ε_n - ChemP) = N_electrons
   - Fermi-Dirac: f(ε) = 1 / (1 + exp[(ε - ChemP)/kT])

4. Compute occupation numbers
   - For each eigenstate n: f_n = 1 / (1 + exp[(ε_n - ChemP)/kT])

5. Reconstruct density matrix
   - DM_{μν} = Σ_n f_n * C_{μn} * C_{νn}
   - This gives DM in the orbital basis

6. Compare reconstructed DM with stored DM
   - Compute: ||DM_reconstructed - DM_stored|| / ||DM_stored||
   - Should be < 1e-6 for numerical accuracy
""")

    print("-"*70)
    print("STORAGE COMPARISON:")
    print("-"*70)
    print(f"Current .rst file size: ~{(data['TNO1']**2 * 8 * (data['SpinP_switch']+1) / 1024):.1f} KB per atom")
    print(f"  - Hamiltonian (H): 50%")
    print(f"  - Density Matrix (DM): 50%")
    print(f"  - ChemP: <0.001% (8 bytes)")
    print(f"\nOptimized storage (H + ChemP only):")
    print(f"  - Hamiltonian (H): 99.999%")
    print(f"  - ChemP: 0.001% (8 bytes)")
    print(f"  - DM: 0% (reconstructed on the fly)")
    print(f"\nStorage savings: ~50%")

    print("\n" + "="*70)
    print("NEXT STEPS FOR FULL IMPLEMENTATION:")
    print("="*70)
    print("""
1. Modify OpenMX restart logic to skip DM reading when flag is set
2. Call diagonalization routine (EigenBand_lapack) after reading H
3. Use existing Fermi-Dirac occupation calculation
4. Reconstruct DM from eigenvectors and occupations
5. Verify accuracy by comparing with original DM
""")

    print("="*70 + "\n")


class RestartFileWriter:
    """Write OpenMX restart files (.rst) with DM set to zero"""

    def __init__(self, filepath: str, data: Dict):
        self.filepath = filepath
        self.data = data

    def write_with_zero_dm(self):
        """Write restart file with DM (Density Matrix) set to zero"""
        with open(self.filepath, 'wb') as f:
            # Write header (10 integers)
            header = [
                self.data['SpinP_switch'],
                self.data['List_YOUSO_23'],
                self.data['List_YOUSO_1'],
                self.data['List_YOUSO_8'],
                self.data['List_YOUSO_7'],
                self.data['atomnum'],
                self.data['wan1'],
                self.data['TNO1'],
                self.data['FNAN'],
                self.data['SO_switch']
            ]
            f.write(struct.pack('10i', *header))

            # Write orbital information for each neighbor
            fnan = self.data['FNAN']
            num_neighbors = fnan + 1

            # Combine all orbital info arrays
            orbital_info = (
                self.data['Gh_AN'] +
                self.data['atv_ijk_x'] +
                self.data['atv_ijk_y'] +
                self.data['atv_ijk_z'] +
                self.data['wan2'] +
                self.data['TNO2']
            )

            num_orbital_blocks = (fnan + 1) * 6
            f.write(struct.pack(f'{num_orbital_blocks}i', *orbital_info))

            # Write Uele (electronic energy)
            f.write(struct.pack('d', self.data['Uele']))

            # Write Kohn-Sham Hamiltonian
            spin_polarized = self.data['SpinP_switch']
            tno1 = self.data['TNO1']

            for spin in range(spin_polarized + 1):
                for h_an in range(fnan + 1):
                    tno2 = self.data['TNO2'][h_an]
                    # Get the H block for this spin and neighbor
                    h_block = self.data['H'][spin][h_an]
                    # Flatten and write the matrix
                    f.write(struct.pack(f'{tno1 * tno2}d', *h_block.flatten()))

            # Write non-collinear SOC Hamiltonian if needed
            if spin_polarized == 3:
                for spin in range(spin_polarized):
                    for h_an in range(fnan + 1):
                        tno2 = self.data['TNO2'][h_an]
                        h_block = self.data['iHNL'][spin][h_an]
                        f.write(struct.pack(f'{tno1 * tno2}d', *h_block.flatten()))

            # Write Density Matrix (DM) - SET TO ZEROES to maintain compatibility
            for spin in range(spin_polarized + 1):
                for h_an in range(fnan + 1):
                    tno2 = self.data['TNO2'][h_an]
                    # Create zero matrix of appropriate size instead of original DM
                    zero_dm = np.zeros((tno1, tno2), dtype=np.float64)
                    # Flatten and write the zero matrix
                    f.write(struct.pack(f'{tno1 * tno2}d', *zero_dm.flatten()))

            # NOTE: iDM is NOT written to RST files

            print(f"Restart file written with zero DM to: {self.filepath}")


def create_rst_with_zero_dm(input_filepath: str, output_filepath: str):
    """Create a new restart file with DM set to zero"""
    # Read the original file
    reader = RestartFileReader(input_filepath)
    data = reader.read()

    # Create writer and write new file with DM set to zero
    writer = RestartFileWriter(output_filepath, data)
    writer.write_with_zero_dm()

    print(f"Created new restart file with zero DM: {output_filepath}")
    return output_filepath


def main():
    """Test the restart file reader"""
    import sys

    if len(sys.argv) < 2:
        # Default behavior - use example directory
        rst_dir = "/home/duguex/HamGNN/restart_from_ham/example_data/openmx_cal/openmx_rst"
        system_name = "openmx"

        print("Reading restart files...")
        restart_data = read_restart_directory(rst_dir, system_name)

        print(f"\nTotal restart files read: {len(restart_data)}")

        # Print info for first atom
        if 1 in restart_data:
            print("\n=== First atom (atom 1) ===")
            reader = RestartFileReader(os.path.join(rst_dir, f"{system_name}.rst1"))
            data = reader.read()
            reader.print_info()
    elif '--verify' in sys.argv:
        filepath = sys.argv[1]
        if not os.path.exists(filepath):
            print(f"Error: File '{filepath}' not found!")
            sys.exit(1)
        verify_dm_reconstruction(filepath)
    elif '--set-zero-dm' in sys.argv:
        if len(sys.argv) < 3:
            print("Usage: python read_restart.py --set-zero-dm <input_file> [output_file]")
            sys.exit(1)

        input_file = sys.argv[2]
        output_file = sys.argv[3] if len(sys.argv) > 3 else input_file.replace('.rst', '_zero_dm.rst')

        if not os.path.exists(input_file):
            print(f"Error: Input file '{input_file}' not found!")
            sys.exit(1)

        create_rst_with_zero_dm(input_file, output_file)
    else:
        filepath = sys.argv[1]
        if not os.path.exists(filepath):
            print(f"Error: File '{filepath}' not found!")
            sys.exit(1)
        reader = RestartFileReader(filepath)
        data = reader.read()
        reader.print_info()


if __name__ == "__main__":
    main()