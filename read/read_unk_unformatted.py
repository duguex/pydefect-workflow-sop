import numpy as np
import typing

def read_unk_unformatted(file_path: str) -> tuple[int, np.ndarray]:
    """
    Read wavefunction files (UNKnnnnn.n files) in **unformatted** (binary) format.

    This function reads binary UNK files such as those written by VASP or pw2wannier90
    in default (unformatted) mode. The file structure is:

        - First record: 5 integers (NGX, NGY, NGZ, k-point index, number of bands)
        - Following records: complex wavefunction data for each band, stored in Fortran order

    Arguments:
        file_path (str): Path to the UNK binary file (e.g., 'UNK00001.1')

    Returns:
        k-point index (int)
        complex wavefunction array of shape (NGX, NGY, NGZ, Nb)

    """
    with open(file_path, 'rb') as f:
        # Read the first 6 integers
        header = np.fromfile(f, dtype=np.int32, count=6)
        if len(header) != 6:
            raise ValueError("Invalid UNK file: header too short.")

        ngx, ngy, ngz, _, ik, nbnd = header  # skip 4th value (unknown)
        print(f"Read UNK file: grid={ngx}x{ngy}x{ngz}, k-point={ik}, bands={nbnd}")

        # Total number of grid points per band
        grid_size = ngx * ngy * ngz

        # Read the complex wavefunction data
        # Each band is stored as a sequence of complex numbers (real, imag)
        data = np.fromfile(f, dtype=np.complex128, count=grid_size * nbnd)
        if len(data) != grid_size * nbnd:
            raise ValueError("Invalid UNK file: wavefunction data size mismatch.")

        # Reshape into (ngx, ngy, ngz, nbnd) with Fortran order
        wvfn = data.reshape((ngx, ngy, ngz, nbnd), order='F')

    return ik, wvfn


# Example usage
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python read_unk_unformatted.py <UNK file>")
        sys.exit(1)

    unk_file = sys.argv[1]
    try:
        ik, wvfn = read_unk_unformatted(unk_file)
        print(f"Successfully read k-point {ik} with wavefunction shape {wvfn.shape}")
    except Exception as e:
        print(f"Error reading UNK file: {e}")
        sys.exit(1)