#!/usr/bin/env python3

import ambit

import sys
import numpy as np
from pathlib import Path

def main():
    
    basepath = str(Path(__file__).parent.absolute())

    # all possible input parameters

    IO_PARAMS            = {'problem_type'          : 'solid_flow0d', # solid, fluid, flow0d, solid_flow0d, fluid_flow0d, solid_flow0d_multiscale_gandr
                            'mesh_domain'           : ''+basepath+'/input/blocks_domain.xdmf',
                            'mesh_boundary'         : ''+basepath+'/input/blocks_boundary.xdmf',
                            'fiber_data'            : {'nodal' : [''+basepath+'/file1.txt',''+basepath+'/file2.txt']}, # only for anisotropic solid materials - nodal: fiber input data is stored at node coordinates, elemental: fiber input data is stored at element center
                            'write_results_every'   : 1, # frequency for results output (negative value for no output, 1 for every time step, etc.)
                            'write_results_every_0D': 1, # OPTIONAL: for flow0d results, use write_results_every if not specified
                            'output_path'           : ''+basepath+'/tmp/', # where results are written to
                            'output_path_0D'        : ''+basepath+'/tmp/', # OPTIONAL: for flow0d results, use output_path if not specified
                            'results_to_write'      : ['displacement','velocity','pressure','cauchystress'], # see io_routines.py for what to write
                            'simname'               : 'my_simulation_name'} # how to name the output (attention: there is no warning, results will be overwritten if existent)

    SOLVER_PARAMS_SOLID  = {'solve_type'            : 'direct', # direct, iterative
                            'tol_res'               : 1.0e-8, # residual tolerance for nonlinear solver
                            'tol_inc'               : 1.0e-8, # increment tolerance for nonlinear solver
                            'divergence_continue'   : None, # OPTIONAL: what to apply when Newton diverges: None, PTC ('ptc' can stay False)
                            'ptc'                   : False, # OPTIONAL: if you want to use PTC straight away (independent of divergence_continue)
                            'k_ptc_initial'         : 0.1, # OPTIONAL: initial PTC value that adapts during nonlinear iteration
                            'tol_lin'               : 5.0e-5, # for solve_type 'iterative': linear solver tolerance
                            'print_liniter_every'   : 50, # OPTIONAL: for solve_type 'iterative': how often to print linear iterations
                            'adapt_linsolv_tol'     : True, # OPTIONAL: for solve_type 'iterative': True, False - adapt linear tolerance throughout nonlinear iterations
                            'adapt_factor'          : 0.1} # OPTIONAL: for solve_type 'iterative': adaptation factor for adapt_linsolv_tol (the larger, the more adaptation)
                                
    SOLVER_PARAMS_FLOW0D = {'tol_res'               : 1.0e-6, # residual tolerance for nonlinear solver
                            'tol_inc'               : 1.0e-6} # increment tolerance for nonlinear solver

    TIME_PARAMS_SOLID    = {'maxtime'               : 1.0, # maximum simulation time
                            'numstep'               : 500, # number of steps over maxtime (maxtime/numstep governs the time step size)
                            'numstep_stop'          : 5, # OPTIONAL: if we want the simulation to stop earlier
                            'timint'                : 'genalpha', # time-integration algorithm: genalpha, ost, static
                            'theta_ost'             : 1.0, # One-Step-Theta (ost) time integration factor 
                            'rho_inf_genalpha'      : 0.8, # spectral radius of Generalized-alpha (genalpha) time-integration (governs all other parameters alpha_m, alpha_f, beta, gamma)
                            'avg_genalpga'          : 'trlike'} # OPTIONAL, AND NOT (YET) IMPLEMENTED!!! how to evaluate nonlinear terms in time integrator - trlike: time_factor * f(a_{n+1}) + (1-time_factor) * f(a_{n}), midlike: f(time_factor*a_{n+1}+(1-time_factor)*a_{n}) (currently, only trlike is supported)
    
    TIME_PARAMS_FLOW0D   = {'timint'                : 'ost', # time-integration algorithm: ost
                            'theta_ost'             : 0.5, # One-Step-Theta time integration factor 
                            'initial_conditions'    : init(), # initial condition dictionary (here defined as function, see below)
                            'initial_file'          : None, # OPTIONAL: if we want to read initial conditions from a file (overwrites above specified dict)
                            'eps_periodic'          : 1.0e-3, # OPTIONAL: cardiac cycle periodicity tolerance
                            'periodic_checktype'    : None} # OPTIONAL: None, 'allvar', 'pQvar'

    MODEL_PARAMS_FLOW0D  = {'modeltype'             : 'syspul', # 2elwindkessel, 4elwindkesselLsZ, 4elwindkesselLpZ, syspul, syspulcap, syspulcap2
                            'parameters'            : param(), # parameter dictionary (here defined as function, see below)
                            'chamber_models'        : {'lv' : '3D_fem', 'rv' : '3D_fem', 'la' : '0D_elast', 'ra' : '0D_elast'}, # only for syspul* models - 3D_fem: chamber is 3D, 0D_elast: chamber is 0D elastance model, prescr_elast: chamber is 0D elastance model with prescribed elastance over time
                            'chamber_interfaces'    : {'lv' : 1, 'rv' : 1, 'la' : 0, 'ra' : 0},
                            'prescribed_variables'  : {'q_vin_l' : 1}, # OPTIONAL: in case we want to prescribe values: variable name, and time curve number (define below)
                            'perturb_type'          : None, # OPTIONAL: mr, ms, ar, as
                            'perturb_after_cylce'   : 2} # OPTIONAL: after which cycle to induce the perturbation / disease / cardiovascular state change...

    FEM_PARAMS           = {'order_disp'            : 1, # order of displacement interpolation (solid mechanics)
                            'order_vel'             : 1, # order of velocity interpolation (fluid mechanics)
                            'order_pres'            : 1, # order of pressure interpolation (solid, fluid mechanics)
                            'quad_degree'           : 1, # quadrature degree q (number of integration points: n(q) = ((q+2)//2)**dim) --> can be 1 for linear tets, should be >= 3 for linear hexes, should be >= 4 for quadratic tets/hexes
                            'incompressible_2field' : False, # if we want to use a 2-field functional for pressure dofs (always applies for fluid, optional for solid mechanics)
                            'prestress_initial'     : False} # OPTIONAL: if we want to use MULF prestressing (Gee et al. 2010) prior to solving a dynamic/other kind of solid or solid-coupled problem (experimental, not thoroughly tested!)
    
    COUPLING_PARAMS      = {'surface_ids'           : [1,2], # for syspul* models: order is lv, rv, la, ra (has to be consistent with chamber_models dict)
                            'surface_p_ids'         : [1,2], # optional, if pressure should be applied to different surface than that from which the volume/flux is measured from... if not specified, the same as surface_ids
                            'cq_factor'             : [1.,1.], # if we want to scale the 3D volume or flux (e.g. for 2D solid models)
                            'coupling_quantity'     : 'volume', # volume, flux, pressure (former need 'monolithic_direct', latter needs 'monolithic_lagrange' as coupling_type)
                            'coupling_type'         : 'monolithic_direct'} # monolithic_direct, monolithic_lagrange

    MULTISCALE_GR_PARAMS = {'gandr_trigger_phase'   : 'end_diastole', # end_diastole, end_systole
                            'numcycles'             : 2,
                            'tol_small'             : 1.0e-3, # cycle error tolerance: overrides eps_periodic from TIME_PARAMS_FLOW0D
                            'tol_large'             : 1.0e-3, # growth rate tolerance
                            'tol_outer'             : 1.0e-3}

                            # - MATn has to correspond to subdomain id n (set by the flags in Attribute section of *_domain.xdmf file - so if you have x mats, you need ids ranging from 1,...,x)
                            # - one MAT can be decomposed into submats, see examples below (additive stress contributions)
                            # - for solid: if you use a deviatoric (_dev) mat, you should also use EITHER a volumetric (_vol) mat, too, OR set incompressible_2field in FEM_PARAMS to 'True' and then only use a _dev mat and MUST NOT use a _vol mat! (if incompressible_2field is 'True', then all materials have to be treated perfectly incompressible currently)
                            # - for fluid: incompressible_2field is always on, and you only can define a Newtonian fluid ('newtonian') with dynamic viscosity 'eta'
                            # - for dynamics, you need to specify a mat called 'inertia' and set the density ('rho0' in solid, 'rho' in fluid dynamics)
                            # - material can also be inelastic and growth ('growth')
                            # - see solid_material.py or fluid_material.py for material laws available (and their parameters), and feel free to implement/add new strain energy functions or laws fairly quickly
    MATERIALS            = {'MAT1' : {'holzapfelogden_dev' : {'a_0' : 0.059, 'b_0' : 8.023, 'a_f' : 18.472, 'b_f' : 16.026, 'a_s' : 2.481, 'b_s' : 11.120, 'a_fs' : 0.216, 'b_fs' : 11.436, 'fiber_comp' : False},
                                      'sussmanbathe_vol'   : {'kappa' : 1.0e3},
                                      'active_fiber'       : {'sigma0' : 50.0, 'alpha_max' : 15.0, 'alpha_min' : -20.0, 't_contr' : 0.0, 't_relax' : 0.53},
                                      'inertia'            : {'rho0' : 1.0e-6},
                                      'rayleigh_damping'   : {'eta_m' : 0.0, 'eta_k' : 0.0001}},
                            'MAT2' : {'neohooke_dev'       : {'mu' : 10.},
                                      'ogden_vol'          : {'kappa' : 10./(1.-2.*0.49)},
                                      'inertia'            : {'rho0' : 1.0e-6},
                                      'growth'             : {'growth_dir' : 'isotropic', # isotropic, fiber, crossfiber, radial
                                                              'growth_trig' : 'volstress', # fibstretch, volstress, prescribed
                                                              'growth_thres' : 1.01, # critial value above which growth happens (i.e. a critial stretch, stress or whatever depending on the growth trigger)
                                                              'thetamax' : 1.5, # maximum growth stretch
                                                              'thetamin' : 1.0, # minimum growth stretch
                                                              'tau_gr' : 1.0, # growth time constant
                                                              'gamma_gr' : 2.0, # growth nonlinearity
                                                              'tau_gr_rev' : 1000.0, # reverse growth time constant
                                                              'gamma_gr_rev' : 2.0, # reverse growth nonlinearity
                                                              'remodeling_mat' : {'neohooke_dev' : {'mu' : 3.}, # remodeling material
                                                                                  'ogden_vol'    : {'kappa' : 3./(1.-2.*0.49)}}}}}



    # define your load curves here (syntax: tcX refers to curve X, to be used in BC_DICT key 'curve' : [X,0,0], or 'curve' : X)
    # some examples... up to 9 possible (tc1 until tc9 - feel free to implement more in timeintegration.py --> timecurves function if needed...)
    class time_curves():
        
        def tc1(self, t):
            return 3.*t
        
        def tc2(self, t):
            return -5000.0*np.sin(2.*np.pi*t/TIME_PARAMS_SOLID['maxtime'])

        def tc3(self, t): # can be a constant but formally needs t as input
            return 5.
        
        #...

    # bc syntax examples
    BC_DICT              = { 'dirichlet' : [{'id' : 1, 'dir' : 'all', 'val' : 0.}, # either curve or val
                                            {'id' : 2, 'dir' : 'y', 'val' : 0.}, # either curve or val
                                            {'id' : 3, 'dir' : 'z', 'curve' : 1}], # either curve or val
                            # Neumann can be - pk1 with dir xyz (then use 'curve' : [xcurve-num, ycurve-num, zcurve-num] with 0 meaning zero),
                            #                - pk1 with dir normal (then use 'curve' : curve-num with 0 meaning zero)
                            'neumann'    : [{'type' : 'pk1', 'id' : 3, 'dir' : 'xyz', 'curve' : [1,0,0]},
                                            {'type' : 'pk1', 'id' : 2, 'dir' : 'normal', 'curve' : 1}
                                            {'type' : 'true', 'id' : 2, 'dir' : 'normal', 'curve' : 1}],
                            # Robib BC can be either spring or dashpot, both either in xyz or normal direction
                            'robin'      : [{'type' : 'spring', 'id' : 3, 'dir' : 'normal', 'stiff' : 0.075},
                                            {'type' : 'dashpot', 'id' : 3, 'dir' : 'xyz', 'visc' : 0.005}] }

    # problem setup
    problem = ambit.Ambit(IO_PARAMS, [TIME_PARAMS_SOLID, TIME_PARAMS_FLOW0D], [SOLVER_PARAMS_SOLID, SOLVER_PARAMS_FLOW0D], FEM_PARAMS, [MATERIALS, MODEL_PARAMS_FLOW0D], BC_DICT, time_curves=time_curves(), coupling_params=COUPLING_PARAMS)
    
    # problem solve
    problem.solve_problem()


# syspul circulation model initial condition and parameter dicts...

def init():
    
    return {'q_vin_l_0' : 1.1549454594333263E+04,
            'p_at_l_0' : 3.8580961077622145E-01,
            'q_vout_l_0' : -1.0552685263595845E+00,
            'p_v_l_0' : 3.7426015618188813E-01,
            'p_ar_sys_0' : 1.0926945419777734E+01,
            'q_ar_sys_0' : 7.2237210814547114E+04,
            'p_ven_sys_0' : 2.2875736545217800E+00,
            'q_ven_sys_0' : 8.5022643486798144E+04,
            'q_vin_r_0' : 4.1097788677528049E+04,
            'p_at_r_0' : 2.4703021083862464E-01,
            'q_vout_r_0' : -2.0242075369768467E-01,
            'p_v_r_0' : 2.0593242216109664E-01,
            'p_ar_pul_0' : 2.2301399591379436E+00,
            'q_ar_pul_0' : 3.6242987765574515E+04,
            'p_ven_pul_0' : 1.6864951426543255E+00,
            'q_ven_pul_0' : 8.6712368791873596E+04}

def param():
    
    R_ar_sys = 120.0e-6
    tau_ar_sys = 1.65242332
    tau_ar_pul = 0.3

    # Diss Hirschvogel tab. 2.7
    C_ar_sys = tau_ar_sys/R_ar_sys
    Z_ar_sys = R_ar_sys/20.
    R_ven_sys = R_ar_sys/5.
    C_ven_sys = 30.*C_ar_sys
    R_ar_pul = R_ar_sys/8.
    C_ar_pul = tau_ar_pul/R_ar_pul
    Z_ar_pul = 0.
    R_ven_pul = R_ar_pul
    C_ven_pul = 2.5*C_ar_pul

    L_ar_sys = 0.667e-6
    L_ven_sys = 0.
    L_ar_pul = 0.
    L_ven_pul = 0.

    # atrial elastances
    E_at_A_l, E_at_min_l = 20.0e-6, 9.0e-6
    E_at_A_r, E_at_min_r = 10.0e-6, 8.0e-6

    # timings
    t_ed = 0.2
    t_es = 0.53


    return {'R_ar_sys' : R_ar_sys,
            'C_ar_sys' : C_ar_sys,
            'L_ar_sys' : L_ar_sys,
            'Z_ar_sys' : Z_ar_sys,
            'R_ar_pul' : R_ar_pul,
            'C_ar_pul' : C_ar_pul,
            'L_ar_pul' : L_ar_pul,
            'Z_ar_pul' : Z_ar_pul,
            'R_ven_sys' : R_ven_sys,
            'C_ven_sys' : C_ven_sys,
            'L_ven_sys' : L_ven_sys,
            'R_ven_pul' : R_ven_pul,
            'C_ven_pul' : C_ven_pul,
            'L_ven_pul' : L_ven_pul,
            # atrial elastances
            'E_at_max_l' : E_at_min_l+E_at_A_l,
            'E_at_min_l' : E_at_min_l,
            'E_at_max_r' : E_at_min_r+E_at_A_r,
            'E_at_min_r' : E_at_min_r,
            # ventricular elastances
            'E_v_max_l' : 7.0e-5,
            'E_v_min_l' : 12.0e-6,
            'E_v_max_r' : 3.0e-5,
            'E_v_min_r' : 10.0e-6,
            # valve resistances
            'R_vin_l_min' : 1.0e-6,
            'R_vin_l_max' : 1.0e1,
            'R_vout_l_min' : 1.0e-6,
            'R_vout_l_max' : 1.0e1,
            'R_vin_r_min' : 1.0e-6,
            'R_vin_r_max' : 1.0e1,
            'R_vout_r_min' : 1.0e-6,
            'R_vout_r_max' : 1.0e1,
            # timings
            't_ed' : t_ed,
            't_es' : t_es,
            'T_cycl' : 1.0,
            # unstressed compartment volumes (for post-processing)
            'V_at_l_u' : 0.0,
            'V_at_r_u' : 0.0,
            'V_v_l_u' : 0.0,
            'V_v_r_u' : 0.0,
            'V_ar_sys_u' : 0.0,
            'V_ar_pul_u' : 0.0,
            'V_ven_sys_u' : 0.0,
            'V_ven_pul_u' : 0.0}



if __name__ == "__main__":
    
    main()
