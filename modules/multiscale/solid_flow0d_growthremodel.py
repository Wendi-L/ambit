#!/usr/bin/env python3

import time, sys, copy
import numpy as np
from dolfinx import FunctionSpace, VectorFunctionSpace, TensorFunctionSpace, Function, DirichletBC
from dolfinx.fem import assemble_scalar
from ufl import TrialFunction, TestFunction, FiniteElement, derivative, diff, dx, ds, as_ufl
from petsc4py import PETSc

import utilities
import solver_nonlin
import expression
from projection import project
from mpiroutines import allgather_vec

from solid import SolidmechanicsProblem, SolidmechanicsSolver
from solid_flow0d import SolidmechanicsFlow0DProblem, SolidmechanicsFlow0DSolver


class SolidmechanicsFlow0DMultiscaleGrowthRemodelingProblem():

    def __init__(self, io_params, time_params_solid_small, time_params_solid_large, time_params_flow0d, fem_params, constitutive_models, model_params_flow0d, bc_dict, time_curves, coupling_params, multiscale_params, comm=None):
        
        self.comm = comm
        
        self.problem_physics = 'ssss_flow0d'
        
        gandr_trigger_phase = multiscale_params['gandr_trigger_phase']
        
        self.N_cycles = multiscale_params['numcycles']
        
        constitutive_models_large = copy.deepcopy(constitutive_models)

        # set growth for small dynamic scale
        for n in range(len(constitutive_models)):
            growth_trig = constitutive_models['MAT'+str(n+1)+'']['growth']['growth_trig']
            constitutive_models['MAT'+str(n+1)+'']['growth']['growth_trig'] = 'prescribed_multiscale'
            constitutive_models['MAT'+str(n+1)+'']['growth']['growth_settrig'] = growth_trig

        # remove any dynamics from large scale constitutive models dict
        for n in range(len(constitutive_models_large)):
            try:
                constitutive_models_large['MAT'+str(n+1)+''].pop('inertia')
                constitutive_models_large['MAT'+str(n+1)+''].pop('rayleigh_damping')
            except:
                pass
            
            # we must have a growth law in each material
            assert('growth' in constitutive_models_large['MAT'+str(n+1)+''].keys())

            # set active stress to prescribed on large scale
            try:
                constitutive_models_large['MAT'+str(n+1)+'']['active_fiber']['prescribed_multiscale'] = True
                constitutive_models_large['MAT'+str(n+1)+'']['active_iso']['prescribed_multiscale'] = True
            except:
                pass

        fem_params_large = copy.deepcopy(fem_params)
        # no prestress on the large scale
        try: fem_params_large.pop('prestress_initial')
        except: pass
            
        # we have to be quasi-static on the large scale!
        assert(time_params_solid_large['timint'] == 'static')

        # initialize problem instances
        self.pbsmall = SolidmechanicsFlow0DProblem(io_params, time_params_solid_small, time_params_flow0d, fem_params, constitutive_models, model_params_flow0d, bc_dict, time_curves, coupling_params, comm=self.comm)
        self.pblarge = SolidmechanicsProblem(io_params, time_params_solid_large, fem_params_large, constitutive_models_large, bc_dict, time_curves, comm=self.comm)

        self.simname_small = self.pbsmall.pbs.io.simname + '_small'
        self.simname_large = self.pblarge.io.simname + '_large'

        if gandr_trigger_phase == 'end_diastole':
            self.pbsmall.t_gandr_setpoint = self.pbsmall.pbf.cardvasc0D.t_ed
        elif gandr_trigger_phase == 'end_systole':
            self.pbsmall.t_gandr_setpoint = self.pbsmall.pbf.cardvasc0D.t_es
        else:
            raise NameError("Unknown growth multiscale_trigger_phase")

        self.set_variational_forms_and_jacobians()

        
    # defines the solid and monolithic coupling forms for 0D flow and solid mechanics
    def set_variational_forms_and_jacobians(self):
        
        # all coupled 3D-0D forms for small timescale
        self.pbsmall.set_variational_forms_and_jacobians()
        # large timescale solid forms
        self.pblarge.set_variational_forms_and_jacobians()

        # add constant Neumann terms for large scale problem (trigger pressures)
        self.neumann_funcs = []
        w_neumann = as_ufl(0)
        for i in range(len(self.pbsmall.surface_p_ids)):
            
            self.neumann_funcs.append(Function(self.pblarge.Vd_scalar))
            
            ds_ = ds(subdomain_data=self.pblarge.io.mt_b1, subdomain_id=self.pbsmall.surface_p_ids[i], metadata={'quadrature_degree': self.pblarge.quad_degree})
            
            w_neumann += self.pblarge.vf.deltaW_ext_neumann_true(self.pblarge.ki.J(self.pblarge.u), self.pblarge.ki.F(self.pblarge.u), self.neumann_funcs[-1], ds_)

        self.pblarge.weakform_u -= w_neumann
        self.pblarge.jac_uu -= derivative(w_neumann, self.pblarge.u, self.pblarge.du)



class SolidmechanicsFlow0DMultiscaleGrowthRemodelingSolver():

    def __init__(self, problem, solver_params_solid, solver_params_flow0d):
    
        self.pb = problem
        
        # initialize problem instances
        self.solversmall = SolidmechanicsFlow0DSolver(self.pb.pbsmall, solver_params_solid, solver_params_flow0d)
        self.solverlarge = SolidmechanicsSolver(self.pb.pblarge, solver_params_solid)


    def solve_problem(self):
        
        start = time.time()
        
        # print header
        #utilities.print_problem(self.pb.problem_physics, self.pb.pbs.ndof, self.pb.comm)

        # TODO Finish implementation
        #raise AttributeError("Multiscale G&R not yet fully implemented!")
        
        # multiscale growth and remodeling solid 0D flow main time loop
        for N in range(self.pb.N_cycles):

            wts = time.time()
            
            self.pb.pbsmall.t_prev += (self.pb.pbsmall.pbf.ti.cycle[0]-1) * self.pb.pbsmall.pbf.cardvasc0D.T_cycl

            # change output names
            self.pb.pbsmall.pbs.io.simname = self.pb.simname_small + str(N)
            self.pb.pblarge.io.simname = self.pb.simname_large + str(N)

            # solve small scale 3D-0D coupled solid-flow0d problem with fixed growth
            self.solversmall.solve_problem()
            
            # update large scale variables
            self.pb.pblarge.u.vector.axpby(1.0, 0.0, self.pb.pbsmall.pbs.u_set.vector)
            self.pb.pblarge.u.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            if self.pb.pblarge.incompressible_2field:
                self.pb.pblarge.p.vector.axpby(1.0, 0.0, self.pb.pbsmall.pbs.p_set.vector)
                self.pb.pblarge.p.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

            self.pb.pblarge.tau_a.vector.axpby(1.0, 0.0, self.pb.pbsmall.pbs.tau_a_set.vector)
            self.pb.pblarge.tau_a.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            
            self.pb.pbsmall.pbf.cardvasc0D.set_pressure_fem(self.pb.pbsmall.pbf.s_set, self.pb.pbsmall.pbf.cardvasc0D.v_ids, self.pb.pbsmall.pr0D, self.pb.neumann_funcs)

            self.pb.pblarge.growth_thres.vector.axpby(1.0, 0.0, self.pb.pbsmall.pbs.growth_thres.vector)
            self.pb.pblarge.growth_thres.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

            # solve large scale static G&R solid problem with fixed loads
            self.solverlarge.solve_problem()
            
            u_delta = PETSc.Vec().createMPI(self.pb.pblarge.u.vector.getSize(), bsize=self.pb.pblarge.u.vector.getBlockSize(), comm=self.pb.comm)
            u_delta.waxpy(-1.0, self.pb.pbsmall.pbs.u_set.vector, self.pb.pblarge.u.vector)
            
            
            # update small scale variables
            self.pb.pbsmall.pbs.u.vector.axpby(1.0, 0.0, u_delta)
            self.pb.pbsmall.pbs.u.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            
            # 0D variables s and s_old are already correctly set from the previous small scale run (end values)
            
            
            if self.pb.pbsmall.pbs.incompressible_2field:
                self.pb.pbsmall.pbs.p.vector.axpby(1.0, 0.0, self.pb.pblarge.p.vector)
                self.pb.pbsmall.pbs.p.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)



        if self.pb.comm.rank == 0: # only proc 0 should print this
            print('Time for full multiscale computation: %.4f s (= %.2f min)' % ( time.time()-start, (time.time()-start)/60. ))
            sys.stdout.flush()
