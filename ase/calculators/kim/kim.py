"""
Knowledgebase of Interatomic Models (KIM) Calculator for ASE written by:

Ellad B. Tadmor
Mingjian Wen
Daniel S. Karls
University of Minnesota

This calculator functions as a wrapper that selects an appropriate calculator for a
given KIM model depending on whether it supports the KIM application programming
interface (API) or not. For more information on KIM, visit https://openkim.org.
"""

import re
import os

from ase.data import atomic_masses, atomic_numbers
from ase.calculators.lammpslib import LAMMPSlib
from ase.calculators.lammpsrun import LAMMPS
from ase.calculators.lammps import convert

from .kimmodel import KIMModelCalculator
from . import kimpy_wrappers
from .exceptions import KIMCalculatorError

def KIM(model_name, simulator=None, options=None, debug=False):
    """Calculator wrapper for OpenKIM models

    Returns a suitable calculator that can be used with any model
    archived in the Open Knowledgebase of Interatomic Models (OpenKIM)
    at https://openkim.org.  There are two kinds of models in KIM:
    Portable Models (PMs), which can be used with any KIM API-compliant
    simulator, and Simulator Models (SMs), which are essentially just
    wrappers around native commands in a specific simulator (often
    combined with values for the model parameters).  PMs published on
    openkim.org contain the string '__MO_' in their name, while SMs
    published on openkim.org contain the string '__SM_' in their name.

    Parameters
    ----------
    model_name : str
        The name of the KIM model installed on your system.  KIM models
        published on openkim.org follow a specific naming scheme (see
        https://openkim.org/doc/schema/kim-ids).

    simulator : str, optional
        Used to identify the ASE calculator that will be used.
        Currently supported values include 'kimmodel', 'lammpslib',
        'lammpsrun' and 'asap', and correspond to different calculators
        as follows:

        - kimmodel (default for PMs)
          : :py:mod:`ase.calculators.kim.kimmodel.KIMModelCalculator`

        - lammpsrun (PMs or LAMMPS SMs)
          : :py:mod:`ase.calculators.lammpsrun.LAMMPS`

        - lammpslib (default for LAMMPS SMs)
          : :py:mod:`ase.calculators.lammpslib.LAMMPSlib`

        - asap (PMs)
          : :py:mod:`asap3.Internal.OpenKIMcalculator.OpenKIMcalculator`

        - asap (ASAP SMs)
          : :py:mod:`asap3.Internal.BuiltinPotentials.EMT`

        In general, this argument should be omitted, in which case a
        calculator compatible with the specified model will
        automatically be determined.

    options : dict, optional
        Additional options passed to the initializer of the selected
        calculator.  If ``simulator`` == 'kimmodel', possible options are:

        - ase_neigh (bool)
          : Whether to use the kimpy neighbor list library (False) or
          use ASE's internal neighbor list mechanism (True). Usually
          kimpy's neighbor list library will be faster.  (Default:
          False)

        - neigh_skin_ratio (float)
          : The skin distance used for neighbor list construction,
          expressed as a fraction of the model cutoff (Default: 0.2)

        - release_GIL (bool)
          : Whether to release python GIL.  Releasing the GIL allows a KIM
          model to run with multiple concurrent threads. (Default: False)

        See the ASE LAMMPS calculators doc page
        (https://wiki.fysik.dtu.dk/ase/ase/calculators/lammps.html) for
        available options for the lammpslib and lammpsrun calculators.

    debug: bool, optional
        If True, detailed information is printed to stdout.  If the
        lammpsrun calculator is being used, this also serves as the
        value of the ``keep_tmp_files`` option. (Default: False)

    Returns
    -------
    An ASE calculator, i.e. an instance of a subclass of
    ase.calculators.calculator.Calculator.  Currently, this will be an
    instance of KIMModelCalculator, LAMMPS (the lammpsrun calculator),
    or LAMMPSlib, which are all defined in the ASE codebase, or an
    instance of one of either OpenKIMcalculator or EMT, which are
    defined in the asap3 codebase.

    Raises
    ------
    KIMCalculatorError
        Indicates an error occurred in initializing the calculator,
        e.g. due to incompatible combinations of argument values
    """
    # options set internally in this calculator
    kimmodel_not_allowed_options = ["modelname", "debug"]
    lammpsrun_not_allowed_options = [
        "parameters",
        "files",
        "specorder",
        "keep_tmp_files",
    ]
    lammpslib_not_allowed_options = [
        "lammps_header",
        "lmpcmds",
        "atom_types",
        "log_file",
        "keep_alive",
    ]
    asap_kimpm_not_allowed_options = ["name", "verbose"]
    asap_kimsm_not_allowed_options = ["Params"]
    if options is None:
        options = dict()

    # If this is a KIM Portable Model (supports KIM API), return support through
    # a KIM-compliant simulator
    if _is_portable_model(model_name):
        if simulator is None:  # Default
            simulator = "kimmodel"

        if simulator == "kimmodel":
            _check_conflict_options(options, kimmodel_not_allowed_options, simulator)
            return KIMModelCalculator(model_name, debug=debug, **options)

        elif simulator == "asap":
            _check_conflict_options(options, asap_kimpm_not_allowed_options, simulator)
            return _asap_calculator(
                model_name, model_type="pm", verbose=debug, options=options
            )

        elif simulator == "lammpsrun":

            _check_conflict_options(options, lammpsrun_not_allowed_options, simulator)

            supported_species = _get_kim_pm_supported_species(model_name)

            # Set up kim_init and kim_interactions lines
            parameters = _get_params_for_LAMMPS_calculator(
                model_name,
                supported_units="metal",
                supported_species=supported_species,
                atom_style=None,
            )

            # Return LAMMPS calculator
            return LAMMPS(
                **parameters,
                specorder=supported_species,
                keep_tmp_files=debug,
                **options
            )

        elif simulator == "lammpslib":
            raise KIMCalculatorError(
                '"lammpslib" calculator does not support KIM Portable Models. Try '
                'using the "lammpsrun" calculator.'
            )
        else:
            raise KIMCalculatorError(
                'Unsupported simulator "{}" requested to run KIM Portable Model.'.format(
                    simulator
                )
            )

    #######################################################
    # If we get to here, the model is a KIM Simulator Model
    #######################################################
    (
        simulator_name,
        supported_species,
        supported_units,
        model_defn,
        atom_style,
    ) = _get_simulator_model_info(model_name)

    # Handle default behavior for 'simulator'
    if simulator is None:
        if simulator_name == "ASAP":
            simulator = "asap"
        elif simulator_name == "LAMMPS":
            simulator = "lammpslib"

    if simulator_name == "ASAP":
        # check options
        _check_conflict_options(options, asap_kimsm_not_allowed_options, simulator)

        return _asap_calculator(
            model_name,
            model_type="sm",
            model_defn=model_defn,
            supported_units=supported_units,
        )

    elif simulator_name == "LAMMPS":

        if simulator == "lammpsrun":
            # check options
            _check_conflict_options(options, lammpsrun_not_allowed_options, simulator)

            # Set up kim_init and kim_interactions lines
            parameters = _get_params_for_LAMMPS_calculator(
                model_name, supported_units, supported_species, atom_style
            )

            # Return LAMMPS calculator
            return LAMMPS(
                **parameters, specorder=supported_species, keep_tmp_files=debug
            )

        elif simulator == "lammpslib":
            # check options
            _check_conflict_options(options, lammpslib_not_allowed_options, simulator)

            # Set up LAMMPS header commands lookup table

            # This units command actually has no effect, but is necessary because
            # LAMMPSlib looks in the header lines for units in order to set them
            # internally
            model_init = ["units " + supported_units + os.linesep]

            model_init.append(
                "kim_init {} {}{}".format(model_name, supported_units, os.linesep)
            )
            model_init.append("atom_modify map array sort 0 0" + os.linesep)

            # Assign atom types to species
            atom_types = {}
            for i_s, s in enumerate(supported_species):
                atom_types[s] = i_s + 1

            kim_interactions = [
                "kim_interactions {}".format((" ").join(supported_species))
            ]

            # Return LAMMPSlib calculator
            return LAMMPSlib(
                lammps_header=model_init,
                lammps_name=None,
                lmpcmds=kim_interactions,
                atom_types=atom_types,
                log_file="lammps.log",
                keep_alive=True,
                **options
            )

        else:
            raise KIMCalculatorError(
                'Unknown LAMMPS calculator: "{}".'.format(simulator)
            )

    else:
        raise KIMCalculatorError('Unsupported simulator: "{}".'.format(simulator_name))


def _is_portable_model(model_name):
    """
    Returns True if the model specified is a KIM Portable Model (if it
    is not, then it must be a KIM Simulator Model -- there are no other
    types of models in KIM)
    """
    with kimpy_wrappers.ModelCollections() as col:
        model_type = col.get_item_type(model_name)

    return model_type == kimpy_wrappers.collection_item_type_portableModel


def _get_simulator_model_info(model_name):
    """
    Retrieve Simulator Model metadata including its native simulator,
    supported species, and units
    """
    with kimpy_wrappers.SimulatorModel(model_name) as sm:
        simulator_name = sm.simulator_name
        supported_species = sm.supported_species
        supported_units = sm.supported_units
        atom_style = sm.atom_style
        model_defn = sm.model_defn

    return (simulator_name, supported_species, supported_units, model_defn, atom_style)


def _get_kim_pm_supported_species(model_name):
    """Gets species supported by a KIM Portable Model"""
    with KIMModelCalculator(model_name) as kim_calc:
        supported_species, _ = kim_calc.get_model_supported_species_and_codes()

    return tuple(supported_species)


def get_model_supported_species(model_name):
    """Convenience function for simulator codes"""
    if _is_portable_model(model_name):
        supported_species = _get_kim_pm_supported_species(model_name)
    else:
        _, supported_species, _, _, _ = _get_simulator_model_info(model_name)

    return supported_species


def _asap_calculator(model_name, model_type, **kwargs):
    try:
        import asap3
    except ImportError as e:
        raise ImportError(str(e) + " You need to install asap3 first.")

    if model_type == "pm":

        return asap3.OpenKIMcalculator(
            name=model_name, verbose=kwargs["verbose"], **kwargs["options"]
        )

    elif model_type == "sm":
        model_defn = kwargs["model_defn"]
        supported_units = kwargs["supported_units"]

        # Verify units (ASAP models are expected to work with "ase" units)
        if supported_units != "ase":
            raise KIMCalculatorError(
                'KIM Simulator Model units are "{}", but expected to '
                'be "ase" for ASAP.'.format(supported_units)
            )

        # Check model_defn to make sure there's only one element in it that is a
        # non-empty string
        if len(model_defn) == 0:
            raise KIMCalculatorError(
                "model-defn is an empty list in metadata file of Simulator Model {}"
                "".format(model_name)
            )
        elif len(model_defn) > 1:
            raise KIMCalculatorError(
                "model-defn should contain only one entry for an ASAP model (found {} "
                "lines)".format(len(model_defn))
            )

        if "" in model_defn:
            raise KIMCalculatorError(
                "model-defn contains an empty string in metadata file of Simulator "
                "Model {}".format(model_name)
            )

        model_defn = model_defn[0].strip()

        # Return calculator
        if model_defn.startswith("EMT"):
            # pull out potential parameters
            mobj = re.search(r"\(([A-Za-z0-9_\(\)]+)\)", model_defn)
            if mobj is None:
                asap_calc = asap3.EMT()
            else:
                pp = mobj.group(1)

                if pp.startswith("EMTRasmussenParameters"):
                    asap_calc = asap3.EMT(parameters=asap3.EMTRasmussenParameters())
                elif pp.startswith("EMTMetalGlassParameters"):
                    asap_calc = asap3.EMT(parameters=asap3.EMTMetalGlassParameters())
                else:
                    raise KIMCalculatorError(
                        'Unknown model "{}" for simulator ASAP.'.format(model_defn)
                    )

        # Use undocumented feature for the EMT asap_calculators to take the energy of an
        # isolated atoms as zero. (Otherwise it is taken to be that of perfect FCC.)
        asap_calc.set_subtractE0(False)

        return asap_calc


def _get_params_for_LAMMPS_calculator(
    model_name, supported_units, supported_species, atom_style
):
    """
    Extract parameters for LAMMPS calculator from model definition lines.
    Returns a dictionary with entries for "pair_style" and "pair_coeff".
    Expects there to be only one "pair_style" line. There can be multiple
    "pair_coeff" lines (result is returned as a list).
    """
    parameters = {}

    # In case the SM supplied its own atom_style in its model-init -- only needed
    # because lammpsrun writes data files and needs to know the proper format
    if atom_style:
        parameters["atom_style"] = atom_style

    # Set units to prevent them from defaulting to metal
    parameters["units"] = supported_units

    parameters["model_init"] = [
        "kim_init {} {}{}".format(model_name, supported_units, os.linesep)
    ]

    parameters["kim_interactions"] = "kim_interactions {}{}".format(
        (" ").join(supported_species), os.linesep
    )

    # For every species in "supported_species", add an entry to the
    # "masses" key in dictionary "parameters".
    parameters["masses"] = []
    for i, species in enumerate(supported_species):
        if species not in atomic_numbers:
            raise KIMCalculatorError("Unknown element species {}.".format(species))
        massstr = str(
            convert(
                atomic_masses[atomic_numbers[species]], "mass", "ASE", supported_units
            )
        )
        parameters["masses"].append(str(i + 1) + " " + massstr)

    return parameters


def _check_conflict_options(options, not_allowed_options, simulator):
    """Check whether options is in not_allowed options"""
    s1 = set(options)
    s2 = set(not_allowed_options)
    common = s1.intersection(s2)

    if common:
        options_in_not_allowed = ", ".join(['"{}"'.format(s) for s in common])

        msg = (
            'Simulator "{}" does not support argument(s): {} provided in "options", '
            "because it is (they are) determined internally within the KIM "
            "calculator".format(simulator, options_in_not_allowed)
        )

        raise KIMCalculatorError(msg)
