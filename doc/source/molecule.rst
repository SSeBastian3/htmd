Molecule
=================

The Molecule class is a central object in HTMD. Most of HTMD functionalities
are implemented via Molecules.  A Molecule contains a molecular system (e.g.
read from a PDB file) which con of course be composed of several indepdent real
molecules. 

Molecule can read many input format like PDB, PSF, PRMTOP, etc and trajectories files as xtc and soon DCDs.
Molecules can be viewed (VMD or WebGL), aligned, selected, rotated, truncated, appended, and so on.

A very important feature is atomselection. This is identical to the VMD atomselection language, so that it is possible to verify an atomselection visually and then apply it programmatically. 

Contents:

.. toctree::
   :maxdepth: 2

   Molecule  <htmd.molecule.molecule>