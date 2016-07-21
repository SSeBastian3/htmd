# (c) 2015-2016 Acellera Ltd http://www.acellera.com
# All Rights Reserved
# Distributed under HTMD Software License Agreement
# No redistribution in whole or part
#
from __future__ import print_function

from htmd.home import home
import numpy as np
import os
import os.path as path
from htmd.molecule.util import _missingSegID, sequenceID
import shutil
from htmd.builder.builder import detectDisulfideBonds
from htmd.builder.builder import _checkMixedSegment
from subprocess import call
from htmd.molecule.molecule import Molecule
from htmd.builder.ionize import ionize as ionizef, ionizePlace
import logging
logger = logging.getLogger(__name__)


def listFiles():
    """ Lists all available AMBER forcefield files
    """
    amberhome = os.environ.get('AMBERHOME')
    if amberhome is None:
        raise NameError('AMBERHOME environment variable is not defined')

    amberdir = path.join(amberhome, 'dat', 'leap', 'cmd')
    ffs = os.listdir(amberdir)
    ffs = [f for f in ffs if path.isfile(path.join(amberdir, f))]
    print('---- Forcefield files list: ' + path.join(amberdir, '') + ' ----')
    for f in ffs:
        print(f)


def build(mol, ff=None, topo=None, param=None, prefix='structure', outdir='./', caps=None, ionize=True, saltconc=0,
          saltanion=None, saltcation=None, disulfide=None, tleap='tleap', execute=True):
    """ Builds a system for AMBER

    Uses tleap to build a system for AMBER. Additionally it allows the user to ionize and add disulfide bridges.

    Parameters
    ----------
    mol : :class:`Molecule <htmd.molecule.molecule.Molecule>` object
        The Molecule object containing the system
    ff : list of str
        A list of leaprc forcefield files. Default: ['leaprc.lipid14', 'leaprc.ff14SB', 'leaprc.gaff']
    topo : list of str
        A list of topology `prepi` files.
    param : list of str
        A list of parameter `frcmod` files.
    prefix : str
        The prefix for the generated pdb and psf files
    outdir : str
        The path to the output directory
    caps : dict
        A dictionary with keys segids and values lists of strings describing the caps for a particular protein segment.
        e.g. caps['P'] = ['ACE', 'NME']. Default: will apply ACE and NME caps to every protein segment.
    ionize : bool
        Enable or disable ionization
    saltconc : float
        Salt concentration to add to the system after neutralization.
    saltanion : {'Cl-'}
        The anion type. Please use only AMBER ion atom names.
    saltcation : {'Na+', 'K+', 'Cs+'}
        The cation type. Please use only AMBER ion atom names.
    disulfide : np.ndarray
        If None it will guess disulfide bonds. Otherwise provide a 2D array where each row is a pair of atom indexes that makes a disulfide bond
    tleap : str
        Path to tleap executable used to build the system for AMBER
    execute : bool
        Disable building. Will only write out the input script needed by tleap. Does not include ionization.

    Returns
    -------
    molbuilt : :class:`Molecule <htmd.molecule.molecule.Molecule>` object
        The built system in a Molecule object

    Example
    -------
    >>> ffs = ['leaprc.lipid14', 'leaprc.ff14SB', 'leaprc.gaff']
    >>> molbuilt = amber.build(mol, ff=ffs, outdir='/tmp/build', saltconc=0.15)
    """
    # Remove pdb bonds!
    mol = mol.copy()
    mol.bonds = np.empty((0, 2), dtype=np.uint32)
    if shutil.which(tleap) is None:
        raise NameError('Could not find executable: `' + tleap + '` in the PATH. Cannot build for AMBER.')
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    _cleanOutDir(outdir)
    if ff is None:
        ff = ['leaprc.lipid14', 'leaprc.ff14SB', 'leaprc.gaff']
    if topo is None:
        topo = []
    if param is None:
        param = []
    if caps is None:
        caps = _defaultProteinCaps(mol)

    _missingSegID(mol)
    _checkMixedSegment(mol)

    logger.info('Converting CHARMM membranes to AMBER.')
    mol = _charmmLipid2Amber(mol)

    #_checkProteinGaps(mol)
    _applyProteinCaps(mol, caps)

    f = open(path.join(outdir, 'tleap.in'), 'w')
    f.write('# tleap file generated by amber.build\n')

    # Printing out the forcefields
    for force in ff:
        f.write('source ' + force + '\n')
    f.write('\n')

    # Loading TIP3P water parameters
    f.write('# Loading ions and TIP3P water parameters\n')
    f.write('loadamberparams frcmod.ionsjc_tip3p\n\n')

    # Loading user parameters
    f.write('# Loading parameter files\n')
    for p in param:
        shutil.copy(p, outdir)
        f.write('loadamberparams ' + path.basename(p) + '\n')
    f.write('\n')

    # Printing out topologies
    f.write('# Loading prepi topologies\n')
    for t in topo:
        shutil.copy(t, outdir)
        f.write('loadamberprep ' + path.basename(t) + '\n')
    f.write('\n')

    # Printing and loading the PDB file. AMBER can work with a single PDB file if the segments are separate by TER
    logger.info('Writing PDB file for input to tleap.')
    pdbname = path.join(outdir, 'input.pdb')
    mol.write(pdbname)
    if not os.path.isfile(pdbname):
        raise NameError('Could not write a PDB file out of the given Molecule.')
    f.write('# Loading the system\n')
    f.write('mol = loadpdb input.pdb\n\n')

    # Printing out patches for the disulfide bridges
    if disulfide is None and not ionize:
        logger.info('Detecting disulfide bonds.')
        disulfide = detectDisulfideBonds(mol)

    if not ionize and len(disulfide) != 0:  # Only make disu bonds after ionizing!
        f.write('# Adding disulfide bonds\n')
        for d in disulfide:
            # Convert to stupid amber residue numbering
            uqseqid = sequenceID((mol.resid, mol.insertion, mol.segid)) + mol.resid[0] - 1
            uqres1 = int(np.unique(uqseqid[mol.atomselect('segid {} and resid {}'.format(d.segid1, d.resid1))]))
            uqres2 = int(np.unique(uqseqid[mol.atomselect('segid {} and resid {}'.format(d.segid2, d.resid2))]))
            # Rename the CYS to CYX if there is a disulfide bond
            mol.set('resname', 'CYX', sel='segid {} and resid {}'.format(d.segid1, d.resid1))
            mol.set('resname', 'CYX', sel='segid {} and resid {}'.format(d.segid2, d.resid2))
            f.write('bond mol.{}.SG mol.{}.SG\n'.format(uqres1, uqres2))
        f.write('\n')

    f.write('# Writing out the results\n')
    f.write('saveamberparm mol ' + prefix + '.prmtop ' + prefix + '.crd\n')
    f.write('quit')
    f.close()

    molbuilt = None
    if execute:
        logpath = os.path.abspath('{}/log.txt'.format(outdir))
        logger.info('Starting the build.')
        currdir = os.getcwd()
        os.chdir(outdir)
        f = open(logpath, 'w')
        try:
            call([tleap, '-f', './tleap.in'], stdout=f)
        except:
            raise NameError('tleap failed at execution')
        f.close()
        os.chdir(currdir)
        logger.info('Finished building.')

        if path.getsize(path.join(outdir, 'structure.crd')) != 0 and path.getsize(path.join(outdir, 'structure.prmtop')) != 0:
            molbuilt = Molecule(path.join(outdir, 'structure.prmtop'))
            molbuilt.read(path.join(outdir, 'structure.crd'))
        else:
            raise NameError('No structure pdb/prmtop file was generated. Check {} for errors in building.'.format(logpath))

        if ionize:
            shutil.move(path.join(outdir, 'structure.crd'), path.join(outdir, 'structure.noions.crd'))
            shutil.move(path.join(outdir, 'structure.prmtop'), path.join(outdir, 'structure.noions.prmtop'))
            totalcharge = np.sum(molbuilt.charge)
            nwater = np.sum(molbuilt.atomselect('water and noh'))
            anion, cation, anionatom, cationatom, nanion, ncation = ionizef(totalcharge, nwater, saltconc=saltconc, ff='amber', anion=saltanion, cation=saltcation)
            newmol = ionizePlace(mol, anion, cation, anionatom, cationatom, nanion, ncation)
            # Redo the whole build but now with ions included
            return build(newmol, ff=ff, topo=topo, param=param, prefix=prefix, outdir=outdir, caps={}, ionize=False,
                         execute=execute, saltconc=saltconc, disulfide=disulfide, tleap=tleap)
    molbuilt.write(path.join(outdir, 'structure.pdb'))
    return molbuilt


def _applyProteinCaps(mol, caps):

    # AMBER capping
    # =============
    # This is the (horrible) way of adding caps in tleap:
    # For now, this is hardwired for ACE and NME
    # 1. Change one of the hydrogens of the N terminal (H[T]?[123]) to the ACE C atom, giving it a new resid
    # 2. Change the OXT (or OT1) oxygen of the C terminal to the N atom of NME, giving it a new resid
    # 3. Reorder to put the new atoms first and last
    # 4. Remove the lingering hydrogens of the N terminal

    # Define the atoms to be replaced (0 and 1 corresponds to N- and C-terminal caps)
    terminalatoms = ['H1 H2 H3 HT1 HT2 HT3',
                     'OXT OT1']  # XPLOR names for H[123] and OXT are HT[123] and OT1, respectively.
    capresname = ['ACE', 'NME']
    capatomtype = ['C', 'N']

    # For each caps definition
    for seg in caps:
        # Get the segment
        segment = mol.atomselect('segid {}'.format(seg), indexes=True)
        # Test segment
        if len(segment) == 0:
            raise RuntimeError('There is no segment {} in the molecule.'.format(seg))
        if len(mol.atomselect('protein and segid {}'.format(seg), indexes=True)) == 0:
            raise RuntimeError(
                'Segment {} is not protein. Capping for non-protein segments is not supported.'.format(seg))
        # Get info on segment and its terminals
        resids = np.unique(mol.get('resid', sel=segment))
        terminalids = [segment[0], segment[-1]]
        terminalresids = [np.min(resids), np.max(resids)]
        # For each cap
        for i, cap in enumerate(caps[seg]):
            # In case there is no cap defined
            if cap is None or cap == '':
                logger.warning(
                    'No cap provided for resid {} on segment {}. Did not apply it.'.format(terminalresids[i], seg))
                continue
            # If it is defined, test if supported
            elif cap != capresname[i]:
                raise RuntimeError(
                    'In segment {}, the {} cap is not supported. Try using {} instead.'.format(seg, cap, capresname[i]))
            # Test if cap is already applied
            testcap = mol.atomselect('segid {} and resid {} and resname {}'.format(seg, terminalresids[i], cap),
                                     indexes=True)
            if len(testcap) != 0:
                logger.warning('Cap {} already exists on segment {}. Did not re-apply it.'.format(cap, seg))
                continue
            # Test if the atom to change exists
            termatomsids = mol.atomselect('segid {} and resid {} and name {}'.format(seg,
                                                                                    terminalresids[i],
                                                                                    terminalatoms[i]),
                                          indexes=True)
            if len(termatomsids) == 0:
                raise RuntimeError(
                    'In segment {}, resid {} should have at least one of these atoms: {}. Cannot cap. '
                    'Capping in AMBER requires one of these atoms on the residues that will be capped. '
                    'Consider using the proteinPrepare function to prepare to your molecule before '
                    'building.'.format(seg, terminalresids[i], terminalatoms[i]))

            # Select atom to change, do changes to cap, and change resid
            atomtomod = np.min(termatomsids)
            mol.set('resname', cap, sel=atomtomod)
            mol.set('name', capatomtype[i], sel=atomtomod)
            mol.set('resid', terminalresids[i]-1+2*i, sel=atomtomod)  # if i=0 => resid-1; i=1 => resid+1

            # Reorder
            neworder = np.arange(mol.numAtoms)
            neworder[atomtomod] = terminalids[i]
            neworder[terminalids[i]] = atomtomod
            _reorderMol(mol, neworder)

        # Remove lingering hydrogens in N- terminal (i = 0)
        mol.remove('segid {} and resid {} and name {}'.format(seg, terminalresids[0], terminalatoms[0]))

def _defaultProteinCaps(mol):
    # Defines ACE and NME (neutral terminals) as default for protein segments
    # Of course, this might not be ideal for proteins that require charged terminals

    segsProt = np.unique(mol.get('segid', sel='protein'))
    caps = dict()
    for s in segsProt:
        caps[s] = ['ACE', 'NME']
    return caps


def _cleanOutDir(outdir):
    from glob import glob
    files = glob(os.path.join(outdir, 'structure.*'))
    files += glob(os.path.join(outdir, 'log.*'))
    files += glob(os.path.join(outdir, '*.log'))
    for f in files:
        os.remove(f)


def _charmmLipid2Amber(mol):
    """ Convert a CHARMM lipid membrane to AMBER format

    Parameters
    ----------
    mol : :class:`Molecule <htmd.molecule.molecule.Molecule>` object
        The Molecule object containing the membrane

    Returns
    -------
    newmol : :class:`Molecule <htmd.molecule.molecule.Molecule>` object
        A new Molecule object with the membrane converted to AMBER
    """
    resdict = _readcsvdict(path.join(home(), 'builder', 'charmmlipid2amber.csv'))

    natoms = mol.numAtoms
    neworder = np.array(list(range(natoms)))  # After renaming the atoms and residues I have to reorder them

    begs = np.zeros(natoms, dtype=bool)
    fins = np.zeros(natoms, dtype=bool)
    begters = np.zeros(natoms, dtype=bool)
    finters = np.zeros(natoms, dtype=bool)

    betabackup = mol.beta.copy()

    mol = mol.copy()
    mol.set('beta', sequenceID(mol.resid))
    for res in resdict.keys():
        molresidx = mol.resname == res
        if not np.any(molresidx):
            continue
        names = mol.name.copy()  # Need to make a copy or I accidentally double-modify atoms

        atommap = resdict[res]
        for atom in atommap.keys():
            rule = atommap[atom]

            molatomidx = np.zeros(len(names), dtype=bool)
            molatomidx[molresidx] = names[molresidx] == atom

            mol.set('resname', rule.replaceresname, sel=molatomidx)
            mol.set('name', rule.replaceatom, sel=molatomidx)
            neworder[molatomidx] = rule.order

            if rule.order == 0:  # First atom (with or without ters)
                begs[molatomidx] = True
            if rule.order == rule.natoms - 1:  # Last atom (with or without ters)
                fins[molatomidx] = True
            if rule.order == 0 and rule.ter:  # First atom with ter
                begters[molatomidx] = True
            if rule.order == rule.natoms - 1 and rule.ter:  # Last atom with ter
                finters[molatomidx] = True

    betas = np.unique(mol.beta[begs])
    residuebegs = np.ones(len(betas), dtype=int) * -1
    residuefins = np.ones(len(betas), dtype=int) * -1
    for i in range(len(betas)):
        residuebegs[i] = np.where(mol.beta == betas[i])[0][0]
        residuefins[i] = np.where(mol.beta == betas[i])[0][-1]
    for i in range(len(residuebegs)):
        beg = residuebegs[i]
        fin = residuefins[i] + 1
        neworder[beg:fin] = neworder[beg:fin] + beg
    idx = np.argsort(neworder)
    mol.beta = betabackup
    _reorderMol(mol, idx)

    begters = np.where(begters[idx])[0]  # Sort the begs and ters
    finters = np.where(finters[idx])[0]

    if len(begters) > 999:
        raise NameError('More than 999 lipids. Cannot define separate segments for all of them.')

    for i in range(len(begters)):
        map = np.zeros(len(mol.resid), dtype=bool)
        map[begters[i]:finters[i]+1] = True
        mol.set('resid', sequenceID(mol.get('resname', sel=map)), sel=map)
        mol.set('segid', 'L' + str(i+1), sel=map)

    return mol


def _reorderMol(mol, order):
    for k in mol._append_fields:
        if mol.__dict__[k] is not None and np.size(mol.__dict__[k]) != 0:
            if k == 'coords':
                mol.__dict__[k] = mol.__dict__[k][order, :, :]
            else:
                mol.__dict__[k] = mol.__dict__[k][order]


def _readcsvdict(filename):
    import csv
    from collections import namedtuple
    if os.path.isfile(filename):
        csvfile = open(filename, 'r')
    else:
        raise NameError('File ' + filename + ' does not exist')

    resdict = dict()

    Rule = namedtuple('Rule', ['replaceresname', 'replaceatom', 'order', 'natoms', 'ter'])

    # Skip header line of csv file. Line 2 contains dictionary keys:
    csvfile.readline()
    csvreader = csv.DictReader(csvfile)
    for line in csvreader:
        searchres = line['search'].split()[1]
        searchatm = line['search'].split()[0]
        if searchres not in resdict:
            resdict[searchres] = dict()
        resdict[searchres][searchatm] = Rule(line['replace'].split()[1], line['replace'].split()[0], int(line['order']), int(line['num_atom']), line['TER'] == 'True')
    csvfile.close()

    return resdict


if __name__ == '__main__':
    from htmd.molecule.molecule import Molecule
    from htmd.builder.solvate import solvate
    from htmd.builder.preparation import proteinPrepare
    from htmd.home import home
    from htmd.util import tempname
    import os
    from glob import glob
    import numpy as np
    from htmd.util import diffMolecules

    np.random.seed(1)
    mol = Molecule('3PTB')
    mol.filter('protein')
    mol = proteinPrepare(mol)
    smol = solvate(mol)
    ffs = ['leaprc.lipid14', 'leaprc.ff14SB', 'leaprc.gaff']
    tmpdir = tempname()
    bmol = build(smol, ff=ffs, outdir=tmpdir)

    compare = home(dataDir=os.path.join('test-amber-build', '3PTB'))
    mol = Molecule(os.path.join(compare, 'structure.prmtop'))

    assert np.array_equal(mol.bonds, bmol.bonds)

    assert len(diffMolecules(mol, bmol)) == 0
