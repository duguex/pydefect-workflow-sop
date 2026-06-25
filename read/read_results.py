#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Results Display Tool

本程序用于读取和显示 results.json 文件中的计算结果。

支持的介电常数格式：
1. 新版本（完整）：
   - electronic_dielectric_constant: 电子介电常数（ε∞）
   - ionic_dielectric_constant: 离子介电常数
   - total_dielectric_constant: 总介电常数（ε_electronic + ε_ionic）
   - 对应的张量形式

2. 旧版本（不完整）：
   - dielectric_constant: 仅电子介电常数（标量）
   - dielectric_matrix: 介电矩阵

程序会自动识别格式并显示相应信息。
"""

import os
import json
import argparse


def get_defect_structures(order_path):
    """Read defect structures from order.txt"""
    defects = []
    if os.path.exists(order_path):
        with open(order_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split(' ')
                    if len(parts) >= 2:
                        serial_part = parts[0]
                        if '→' in serial_part:
                            serial = int(serial_part.split('→')[0])
                        else:
                            try:
                                serial = int(serial_part)
                            except ValueError:
                                continue
                        defect_name = parts[1].strip()
                        defects.append((serial, defect_name))
    return defects


def format_matrix(matrix):
    """Format a 3x3 matrix for display"""
    if not matrix or len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        return "Invalid matrix"
    
    lines = []
    for row in matrix:
        formatted_row = "  ".join([f"{val:.6f}" for val in row])
        lines.append(f"  [{formatted_row}]")
    return "\n".join(lines)


def display_results(defect_name, results):
    """Display results in a formatted way"""
    print(f"\n{'='*80}")
    print(f"Results for: {defect_name}")
    print(f"{'='*80}")
    
    section_num = 1
    
    # Basic Energies section
    if any(key in results for key in ['E0', 'E1', 'E2']):
        print(f"\n{section_num}. Basic Energies:")
        print("-" * 40)
        if 'E0' in results:
            print(f"E0 (0_relax): {results['E0']:.8f}")
        if 'E1' in results:
            print(f"E1 (1_relax): {results['E1']:.8f}")
        if 'E2' in results:
            print(f"E2 (2_relax): {results['E2']:.8f}")
        section_num += 1
    
    # Corrected Energies section
    corrected_energy_keys = [k for k in results if k.endswith('_corrected') and k.startswith('E') and len(k) == 12]  # E0_corrected, E1_corrected, E2_corrected
    if corrected_energy_keys:
        print(f"\n{section_num}. Corrected Energies:")
        print("-" * 40)
        if 'E0_corrected' in results:
            print(f"E0_corrected: {results['E0_corrected']:.8f}")
        if 'E1_corrected' in results:
            print(f"E1_corrected: {results['E1_corrected']:.8f}")
        if 'E2_corrected' in results:
            print(f"E2_corrected: {results['E2_corrected']:.8f}")
        section_num += 1
    
    # Band Structure section
    if any(key in results for key in ['vbm', 'cbm', 'gap']):
        print(f"\n{section_num}. Band Structure:")
        print("-" * 40)
        if 'vbm' in results:
            print(f"VBM: {results['vbm']:.6f}")
        if 'cbm' in results:
            print(f"CBM: {results['cbm']:.6f}")
        if 'gap' in results:
            print(f"Band Gap: {results['gap']:.6f}")
        section_num += 1
    
    # Dielectric Properties section
    dielectric_keys = [
        'electronic_dielectric_constant', 'ionic_dielectric_constant', 
        'total_dielectric_constant', 'dielectric_constant',
        'electronic_dielectric_tensor', 'ionic_dielectric_tensor',
        'total_dielectric_tensor', 'dielectric_matrix'
    ]
    if any(key in results for key in dielectric_keys):
        print(f"\n{section_num}. Dielectric Properties:")
        print("-" * 40)
        
        # Display scalar dielectric constants
        if 'electronic_dielectric_constant' in results:
            print(f"Electronic Dielectric Constant (ε_electronic, ε∞): {results['electronic_dielectric_constant']:.6f}")
        if 'ionic_dielectric_constant' in results:
            print(f"Ionic Dielectric Constant (ε_ionic):              {results['ionic_dielectric_constant']:.6f}")
        if 'total_dielectric_constant' in results:
            print(f"Total Dielectric Constant (ε_total):              {results['total_dielectric_constant']:.6f}")
            
            # Verify the relationship and show contributions
            if 'electronic_dielectric_constant' in results and 'ionic_dielectric_constant' in results:
                ele = results['electronic_dielectric_constant']
                ion = results['ionic_dielectric_constant']
                total = results['total_dielectric_constant']
                calculated_total = ele + ion
                diff = abs(total - calculated_total)
                
                print(f"\n  Verification: ε_total = ε_electronic + ε_ionic")
                print(f"  {total:.6f} = {ele:.6f} + {ion:.6f}")
                print(f"  Difference: {diff:.2e}")
                
                # Calculate percentage contributions
                if total > 0:
                    ele_percent = (ele / total) * 100
                    ion_percent = (ion / total) * 100
                    print(f"\n  Contributions to total dielectric constant:")
                    print(f"    Electronic: {ele_percent:.2f}%")
                    print(f"    Ionic:      {ion_percent:.2f}%")
                    
                    # Classify material type based on ionic contribution
                    if ion_percent < 20:
                        material_type = "Covalent-dominated"
                    elif ion_percent < 50:
                        material_type = "Mixed covalent-ionic"
                    else:
                        material_type = "Ionic-dominated"
                    print(f"    Material type: {material_type}")
        elif 'dielectric_constant' in results:
            # Legacy format (old version without electronic/ionic separation)
            print(f"Average Dielectric Constant (legacy): {results['dielectric_constant']:.6f}")
            print(f"  ⚠ Warning: This is from old version (electronic only, incomplete)")
            print(f"  ⚠ Re-run correction.py --mode full to get complete dielectric data")
        
        # Display dielectric tensors
        if 'electronic_dielectric_tensor' in results:
            print("\nElectronic Dielectric Tensor (ε_electronic):")
            print(format_matrix(results['electronic_dielectric_tensor']))
        if 'ionic_dielectric_tensor' in results:
            print("\nIonic Dielectric Tensor (ε_ionic):")
            print(format_matrix(results['ionic_dielectric_tensor']))
        if 'total_dielectric_tensor' in results:
            print("\nTotal Dielectric Tensor (ε_total = ε_electronic + ε_ionic):")
            print(format_matrix(results['total_dielectric_tensor']))
        elif 'dielectric_matrix' in results:
            # Legacy format
            print("\nDielectric Matrix (legacy):")
            print(format_matrix(results['dielectric_matrix']))
            print("  ⚠ Warning: Legacy format (incomplete)")
        
        section_num += 1
    
    # Perfect Calculation section
    if any(key in results for key in ['E_perfect', 'lattice_matrix']):
        print(f"\n{section_num}. Perfect Calculation:")
        print("-" * 40)
        if 'E_perfect' in results:
            print(f"Energy: {results['E_perfect']:.8f}")
        if 'lattice_matrix' in results:
            print("Lattice Matrix:")
            print(format_matrix(results['lattice_matrix']))
        section_num += 1
    
    # Point Charge Corrections section
    point_charge_keys = [k for k in results if k.startswith('point_charge_correction')]
    if point_charge_keys:
        print(f"\n{section_num}. Point Charge Corrections:")
        print("-" * 40)
        for key in sorted(point_charge_keys):
            charge_state = key.replace('point_charge_correction_q_', 'q=')
            print(f"{charge_state}: {results[key]:.8f} eV")
        section_num += 1
    
    # Potential Alignment Corrections section
    alignment_keys = [k for k in results if k.startswith('potential_alignment_correction')]
    if alignment_keys:
        print(f"\n{section_num}. Potential Alignment Corrections:")
        print("-" * 40)
        # Group by calculation type
        relax_keys = [k for k in alignment_keys if '_relax' in k and '_static' not in k]
        static_keys = [k for k in alignment_keys if '_static' in k]
        
        if relax_keys:
            print("  Relax calculations:")
            for key in sorted(relax_keys):
                calc_type = key.replace('potential_alignment_correction_', '')
                print(f"    {calc_type}: {results[key]:.8f} eV")
        
        if static_keys:
            print("  Static calculations:")
            for key in sorted(static_keys):
                calc_type = key.replace('potential_alignment_correction_', '')
                print(f"    {calc_type}: {results[key]:.8f} eV")
        section_num += 1
    
    # Transition Levels (Uncorrected) section
    transition_keys = [k for k in results if k.endswith('_uncorrected')]
    if transition_keys:
        print(f"\n{section_num}. Transition Levels (Uncorrected):")
        print("-" * 40)
        # Group thermodynamic and optical levels
        thermo_keys = [k for k in transition_keys if 'E(' in k]
        optical_keys = [k for k in transition_keys if 'optical' in k]
        
        if thermo_keys:
            print("  Thermodynamic:")
            for key in sorted(thermo_keys):
                print(f"    {key.replace('_uncorrected', '')}: {results[key]:.6f} eV")
        
        if optical_keys:
            print("  Optical:")
            for key in sorted(optical_keys):
                print(f"    {key.replace('_uncorrected', '')}: {results[key]:.6f} eV")
        section_num += 1
    
    # Transition Levels (Corrected) section
    corrected_transition_keys = [k for k in results if k.endswith('_corrected') and ('E(' in k or 'optical' in k)]
    if corrected_transition_keys:
        print(f"\n{section_num}. Transition Levels (Corrected):")
        print("-" * 40)
        # Group thermodynamic and optical levels
        thermo_corrected_keys = [k for k in corrected_transition_keys if 'E(' in k]
        optical_corrected_keys = [k for k in corrected_transition_keys if 'optical' in k]
        
        if thermo_corrected_keys:
            print("  Thermodynamic:")
            for key in sorted(thermo_corrected_keys):
                print(f"    {key.replace('_corrected', '')}: {results[key]:.6f} eV")
        
        if optical_corrected_keys:
            print("  Optical:")
            for key in sorted(optical_corrected_keys):
                print(f"    {key.replace('_corrected', '')}: {results[key]:.6f} eV")
        section_num += 1
    
    # Summary of Corrections section (if both uncorrected and corrected values exist)
    if transition_keys and corrected_transition_keys:
        print(f"\n{section_num}. Correction Summary:")
        print("-" * 40)
        
        # Compare thermodynamic levels
        thermo_pairs = []
        for uncorr_key in [k for k in transition_keys if 'E(' in k]:
            corr_key = uncorr_key.replace('_uncorrected', '_corrected')
            if corr_key in results:
                correction = results[corr_key] - results[uncorr_key]
                level_name = uncorr_key.replace('_uncorrected', '')
                thermo_pairs.append((level_name, correction))
        
        if thermo_pairs:
            print("  Thermodynamic level corrections:")
            for level_name, correction in sorted(thermo_pairs):
                print(f"    {level_name}: {correction:+.6f} eV")
        
        # Compare optical levels
        optical_pairs = []
        for uncorr_key in [k for k in transition_keys if 'optical' in k]:
            corr_key = uncorr_key.replace('_uncorrected', '_corrected')
            if corr_key in results:
                correction = results[corr_key] - results[uncorr_key]
                level_name = uncorr_key.replace('_uncorrected', '')
                optical_pairs.append((level_name, correction))
        
        if optical_pairs:
            print("  Optical level corrections:")
            for level_name, correction in sorted(optical_pairs):
                print(f"    {level_name}: {correction:+.6f} eV")
        section_num += 1
    
    print(f"\n{'='*80}")


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Read and display results from results.json files')
    parser.add_argument('--start', type=int, required=True, help='Start serial number')
    parser.add_argument('--end', type=int, required=True, help='End serial number')
    args = parser.parse_args()
    
    # Get script directory
    voflow_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Path to order.txt
    order_path = os.path.join(voflow_dir, 'order.txt')
    
    # Read defect structures
    defects = get_defect_structures(order_path)
    
    # Set tmp directory path
    tmp_dir = os.path.join(voflow_dir, '..', 'tmp')
    
    # Process each defect structure in the specified range
    print(f"Reading results for structures with serial numbers {args.start} to {args.end}...")
    
    processed_count = 0
    missing_count = 0
    new_format_count = 0  # Count structures with new dielectric format
    old_format_count = 0  # Count structures with old dielectric format
    
    for serial, defect_name in defects:
        if args.start <= serial <= args.end:
            print(f"\nProcessing {defect_name} (serial: {serial})...")
            
            # Construct path to results.json
            results_json_path = os.path.join(tmp_dir, defect_name, 'results.json')
            
            if os.path.exists(results_json_path):
                try:
                    with open(results_json_path, 'r', encoding='utf-8') as f:
                        results = json.load(f)
                    
                    if results:
                        display_results(defect_name, results)
                        processed_count += 1
                        
                        # Check dielectric format
                        if 'total_dielectric_constant' in results:
                            new_format_count += 1
                        elif 'dielectric_constant' in results:
                            old_format_count += 1
                    else:
                        print(f"  Warning: results.json is empty for {defect_name}")
                        missing_count += 1
                except json.JSONDecodeError:
                    print(f"  Error: Invalid JSON in results.json for {defect_name}")
                    missing_count += 1
                except Exception as e:
                    print(f"  Error reading results.json for {defect_name}: {str(e)}")
                    missing_count += 1
            else:
                print(f"  Error: results.json not found for {defect_name}")
                missing_count += 1
    
    # Summary
    print(f"\n{'='*80}")
    print("Summary:")
    print(f"{'='*80}")
    print(f"Total structures in range: {len([d for d in defects if args.start <= d[0] <= args.end])}")
    print(f"Successfully processed: {processed_count}")
    print(f"Missing or invalid results: {missing_count}")
    print(f"\nDielectric Constant Format:")
    print(f"  New format (ε_electronic + ε_ionic): {new_format_count}")
    print(f"  Old format (ε_electronic only):      {old_format_count}")
    if old_format_count > 0:
        print(f"\n  Note: {old_format_count} structure(s) use old format (incomplete)")
        print(f"        Consider re-running correction.py in full mode to update")
    print(f"{'='*80}")


if __name__ == '__main__':
    main()
