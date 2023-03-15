#!/usr/bin/env python3

# Copyright (c) 2019-2023, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import time, sys, math
import numpy as np
from dolfinx import fem
import ufl
from petsc4py import PETSc

import utilities
import solver_nonlin
import expression
from mpiroutines import allgather_vec

from fluid import FluidmechanicsProblem
from ale import AleProblem
from base import solver_base


class FluidmechanicsAleProblem():

    def __init__(self, io_params, time_params, fem_params, constitutive_models_solid, constitutive_models_ale, bc_dict_fluid, bc_dict_ale, time_curves, coupling_params, io, mor_params={}, comm=None):

        self.problem_physics = 'fluid_ale'
        
        self.comm = comm
        
        self.coupling_params = coupling_params
        
        self.fsi_interface = self.coupling_params['surface_ids']

        # initialize problem instances (also sets the variational forms for the fluid problem)
        self.pba = AleProblem(io_params, time_params, fem_params, constitutive_models_ale, bc_dict_ale, time_curves, io, mor_params=mor_params, comm=self.comm)
        self.pbf = FluidmechanicsProblem(io_params, time_params, fem_params, constitutive_models_solid, bc_dict_fluid, time_curves, io, mor_params=mor_params, comm=self.comm, domainvel=[self.pba.w,self.pba.w_old])

        self.io = io

        # indicator for no periodic reference state estimation
        self.noperiodicref = 1

        self.incompressible_2field = self.pbf.incompressible_2field
        self.localsolve = False
        self.have_rom = False

        self.set_variational_forms_and_jacobians()
        
        self.numdof = self.pbf.numdof + self.pba.numdof
        # fluid is 'master' problem - define problem variables based on its values
        self.simname = self.pbf.simname
        self.restart_step = self.pbf.restart_step
        self.numstep_stop = self.pbf.numstep_stop
        self.dt = self.pbf.dt

        
    # defines the monolithic coupling forms for 0D flow and fluid mechanics
    def set_variational_forms_and_jacobians(self):
    
        self.dbcs_coup = fem.dirichletbc(self.pbf.v, fem.locate_dofs_topological(self.pba.V_w, self.io.mesh.topology.dim-1, self.io.mt_b1.indices[self.io.mt_b1.values == self.fsi_interface[0]]))


    def set_forms_solver(self):
        pass


    def assemble_residual_stiffness_main(self):

        r_v, K_vv = self.pbf.assemble_residual_stiffness_main()
        
        r_w, K_ww = self.pba.assemble_residual_stiffness_main(dbcfluid=self.dbcs_coup)
        
        # nested vv-vw,wv-ww matrix - TODO: Form and add in offdiags! we have dependecies!
        K_comb_nest = PETSc.Mat().createNest([[K_vv, None], [None, K_ww]], isrows=None, iscols=None, comm=self.comm)
        K_comb_nest.assemble()
        
        r_comb_nest = PETSc.Vec().createNest([r_v, r_w])
        
        K_comb = PETSc.Mat()
        K_comb_nest.convert("aij", out=K_comb)

        K_comb.assemble()
        
        r_comb = PETSc.Vec().createWithArray(r_comb_nest.getArray())
        r_comb.assemble()
        
        return r_comb, K_comb


    def assemble_residual_stiffness_incompressible(self):
        
        return self.pbf.assemble_residual_stiffness_incompressible()


    ### now the base routines for this problem
                
    def pre_timestep_routines(self):

        self.pbf.pre_timestep_routines()
        self.pba.pre_timestep_routines()


    def read_restart(self, sname, N):

        # fluid + ALE problem
        self.pbf.read_restart(sname, N)
        self.pba.read_restart(sname, N)


    def evaluate_initial(self):

        pass


    def write_output_ini(self):

        self.pbf.write_output_ini()


    def get_time_offset(self):

        return 0.


    def evaluate_pre_solve(self, t):

        self.pbf.evaluate_pre_solve(t)
        self.pba.evaluate_pre_solve(t)
            
            
    def evaluate_post_solve(self, t, N):

        self.pbf.evaluate_post_solve(t, N)
        self.pba.evaluate_post_solve(t, N)


    def set_output_state(self):

        self.pbf.set_output_state()
        self.pba.set_output_state()

            
    def write_output(self, N, t, mesh=False): 

        self.pbf.write_output(N, t)
        self.pba.write_output(N, t)

            
    def update(self):

        # update time step - fluid and ALE
        self.pbf.update()
        self.pba.update()


    def print_to_screen(self):

        self.pbf.print_to_screen()
        self.pba.print_to_screen()
    
    
    def induce_state_change(self):
        
        self.pbf.induce_state_change()
        self.pba.induce_state_change()


    def write_restart(self, sname, N):

        self.pbf.io.write_restart(self.pbf, N)

        
        
    def check_abort(self, t):
        
        self.pbf.check_abort(t)



class FluidmechanicsAleSolver(solver_base):

    def __init__(self, problem, solver_params):
    
        self.pb = problem
        
        self.solver_params = solver_params
        
        self.initialize_nonlinear_solver()


    def initialize_nonlinear_solver(self):
        
        # initialize nonlinear solver class
        self.solnln = solver_nonlin.solver_nonlinear(self.pb, self.pb.pbf.V_v, self.pb.pbf.V_p, solver_params=self.solver_params)


    def solve_initial_state(self):

        # consider consistent initial acceleration
        if self.pb.pbf.timint != 'static' and self.pb.pbf.restart_step == 0:
            # weak form at initial state for consistent initial acceleration solve
            weakform_a = self.pb.pbf.deltaP_kin_old + self.pb.pbf.deltaP_int_old - self.pb.pbf.deltaP_ext_old
            
            jac_a = ufl.derivative(weakform_a, self.pb.pbf.a_old, self.pb.pbf.dv) # actually linear in a_old

            # solve for consistent initial acceleration a_old
            self.solnln.solve_consistent_ini_acc(weakform_a, jac_a, self.pb.pbf.a_old)


    def solve_nonlinear_problem(self, t):
        
        self.solnln.newton(self.pb.pbf.v, self.pb.pbf.p, self.pb.pba.w, t)
        

    def print_timestep_info(self, N, t, wt):

        # print time step info to screen
        self.pb.pbf.ti.print_timestep(N, t, self.solnln.sepstring, self.pb.pbf.numstep, wt=wt)