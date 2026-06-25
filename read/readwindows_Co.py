#! /bin/bash python3
import os,time

#get the BAND.dat
print('running vaspkit to get BAND.dat')
command = 'vaspkit << EOF \n 211 \n EOF'
os.system(command)
time.sleep(1)

#extract fermi energy
with open('OUTCAR','r') as out:
    text= out.readlines()
for i in text:
    if i.startswith(' E-fermi'):
        fermi = float(i.split()[2])
        print("\n******************   fermi energy is " + str(fermi) + '   ******************\n')

#note that the fermi energy has been considered
#it is not considered'
with open('BAND.dat','r') as f:
    content= f.readlines()
k_num = content[1].split()
total_k = int(k_num[4])
print("\t \t min value\t max value")
for var, i in enumerate(content):
    energy = []
    if i.startswith('# Band-Index'):
        index = int(i.split()[2])
        for k in range(var+1, var+1+total_k):
            energy.append(float(content[k].split()[1]) )
        max_value = max(energy); min_value = min(energy)
        print('band:\t' + str(index) + '\t' + str(min_value) + '\t' + str(max_value))
