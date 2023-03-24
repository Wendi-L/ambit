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
from meshutils import gather_surface_dof_indices


class FluidmechanicsAleProblem():

    def __init__(self, io_params, time_params, fem_params, constitutive_models_fluid, constitutive_models_ale, bc_dict_fluid, bc_dict_ale, time_curves, coupling_params, io, mor_params={}, comm=None):

        self.problem_physics = 'fluid_ale'
        
        self.comm = comm
        
        self.coupling_params = coupling_params
        
        try: self.coupling_fluid_ale = self.coupling_params['coupling_fluid_ale']
        except: self.coupling_fluid_ale = {}

        try: self.coupling_ale_fluid = self.coupling_params['coupling_ale_fluid']
        except: self.coupling_ale_fluid = {}

        try: self.fluid_on_deformed = self.coupling_params['fluid_on_deformed']
        except: self.fluid_on_deformed = 'consistent'

        # initialize problem instances (also sets the variational forms for the fluid problem)
        self.pba = AleProblem(io_params, time_params, fem_params, constitutive_models_ale, bc_dict_ale, time_curves, io, mor_params=mor_params, comm=self.comm)
        self.pbf = FluidmechanicsProblem(io_params, time_params, fem_params, constitutive_models_fluid, bc_dict_fluid, time_curves, io, mor_params=mor_params, comm=self.comm, aleproblem=[self.pba,self.fluid_on_deformed])

        # modify results to write...
        self.pbf.results_to_write = io_params['results_to_write'][0]
        self.pba.results_to_write = io_params['results_to_write'][1]

        self.io = io

        # indicator for no periodic reference state estimation
        self.noperiodicref = 1

        self.incompressible_2field = self.pbf.incompressible_2field
        self.localsolve = False
        self.have_rom = False
        
        # NOTE: Fluid and ALE function spaces have to be of the same type, but are different objects
        # for some reason, applying a function from one funtion space as DBC to another function space,
        # errors occur. Therefore, we define these auxiliary variables and interpolate respectively...

        # fluid displacement, but defined within ALE function space
        self.ufa = fem.Function(self.pba.V_u)
        # ALE velocity, but defined within fluid function space
        self.wf = fem.Function(self.pbf.V_v)

        self.set_variational_forms_and_jacobians()
        
        self.numdof = self.pbf.numdof + self.pba.numdof
        # fluid is 'master' problem - define problem variables based on its values
        self.simname = self.pbf.simname
        self.restart_step = self.pbf.restart_step
        self.numstep_stop = self.pbf.numstep_stop
        self.dt = self.pbf.dt
        self.have_rom = self.pbf.have_rom
        if self.have_rom: self.rom = self.pbf.rom

        self.sub_solve = False

        
    def get_problem_var_list(self):
        
        is_ghosted = [True]*3
        return [self.pbf.v.vector, self.pbf.p.vector, self.pba.u.vector], is_ghosted
        
        
    # defines the monolithic coupling forms for 0D flow and fluid mechanics
    def set_variational_forms_and_jacobians(self):

        # any DBC conditions that we want to set from fluid to ALE (mandatory for FSI or FrSI)
        if bool(self.coupling_fluid_ale):
            
            ids_fluid_ale = self.coupling_fluid_ale['surface_ids']

            if self.coupling_fluid_ale['type'] == 'strong_dirichlet':

                dbcs_coup_fluid_ale = []
                for i in range(len(ids_fluid_ale)):
                    dbcs_coup_fluid_ale.append( fem.dirichletbc(self.ufa, fem.locate_dofs_topological(self.pba.V_u, self.io.mesh.topology.dim-1, self.io.mt_b1.indices[self.io.mt_b1.values == ids_fluid_ale[i]])) )
                
                # pay attention to order... first u=uf, then the others... hence re-set!
                self.pba.bc.dbcs = []
                self.pba.bc.dbcs += dbcs_coup_fluid_ale
                # Dirichlet boundary conditions
                if 'dirichlet' in self.pba.bc_dict.keys():
                    self.pba.bc.dirichlet_bcs(self.pba.V_u)

                # NOTE: linearization entries due to strong DBCs of fluid on ALE are currently not considered in the monolithic block matrix!

            elif self.coupling_fluid_ale['type'] == 'weak_dirichlet':
                
                beta = self.coupling_fluid_ale['beta']
                
                work_dbc_nitsche_fluid_ale = ufl.as_ufl(0)
                for i in range(len(ids_fluid_ale)):
                
                    db_ = ufl.ds(subdomain_data=self.pba.io.mt_b1, subdomain_id=ids_fluid_ale[i], metadata={'quadrature_degree': self.pba.quad_degree})
                    for n in range(self.pba.num_domains): # TODO: Does this work in case of multiple subdomains? I guess so, since self.pba.ma[n].stress should be only non-zero for the respective subdomain...
                        work_dbc_nitsche_fluid_ale += self.pba.vf.deltaW_int_nitsche_dirichlet(self.pba.u, self.pbf.ufluid, self.pba.ma[n].stress(self.pba.var_u)[0], beta, db_) # here, ufluid as form is used!
            
                # add to ALE internal virtual work
                self.pba.weakform_u += work_dbc_nitsche_fluid_ale
                # add to ALE jacobian form and define offdiagonal derivative w.r.t. fluid
                self.pba.jac_uu += ufl.derivative(work_dbc_nitsche_fluid_ale, self.pba.u, self.pba.du)
                self.jac_uv = ufl.derivative(work_dbc_nitsche_fluid_ale, self.pbf.v, self.pbf.dv) # only contribution is from weak DBC here!
            
            else:
                raise ValueError("Unkown coupling_fluid_ale option for fluid to ALE!")

        # any DBC conditions that we want to set from ALE to fluid
        if bool(self.coupling_ale_fluid):
            
            ids_ale_fluid = self.coupling_ale_fluid['surface_ids']

            if self.coupling_ale_fluid['type'] == 'strong_dirichlet':

                dbcs_coup_ale_fluid = []
                for i in range(len(ids_ale_fluid)):
                    dbcs_coup_ale_fluid.append( fem.dirichletbc(self.wf, fem.locate_dofs_topological(self.pbf.V_v, self.io.mesh.topology.dim-1, self.io.mt_b1.indices[self.io.mt_b1.values == ids_ale_fluid[i]])) )
                
                # pay attention to order... first v=w, then the others... hence re-set!
                self.pbf.bc.dbcs = []
                self.pbf.bc.dbcs += dbcs_coup_ale_fluid
                # Dirichlet boundary conditions
                if 'dirichlet' in self.pbf.bc_dict.keys():
                    self.pbf.bc.dirichlet_bcs(self.pbf.V_v)
                    
                # NOTE: linearization entries due to strong DBCs of fluid on ALE are currently not considered in the monolithic block matrix!

            elif self.coupling_ale_fluid['type'] == 'weak_dirichlet':
                
                beta = self.coupling_ale_fluid['beta']
                
                work_dbc_nitsche_ale_fluid = ufl.as_ufl(0)
                for i in range(len(ids_ale_fluid)):
                
                    db_ = ufl.ds(subdomain_data=self.pbf.io.mt_b1, subdomain_id=ids_ale_fluid[i], metadata={'quadrature_degree': self.pbf.quad_degree})
                    for n in range(self.pba.num_domains): # TODO: Does this work in case of multiple subdomains? I guess so, since self.pba.ma[n].stress should be only non-zero for the respective subdomain...
                        work_dbc_nitsche_ale_fluid += self.pbf.vf.deltaW_int_nitsche_dirichlet(self.pbf.w, self.pba.wel, self.pbf.ma[n].sigma(self.pbf.var_v), beta, db_) # here, wel as form is used!
            
                # add to fluid internal virtual power
                self.pbf.weakform_v += work_dbc_nitsche_ale_fluid
                # add to fluid jacobian form and define offdiagonal derivative w.r.t. ALE
                self.pbf.jac_vv += ufl.derivative(work_dbc_nitsche_ale_fluid, self.pbf.v, self.pbf.dv)

            else:
                raise ValueError("Unkown coupling_ale_fluid option for ALE to fluid!")

        # derivative of fluid momentum w.r.t. ALE displacement - also includes potential weak Dirichlet or Robin BCs from ALE to fluid!
        self.jac_vu = ufl.derivative(self.pbf.weakform_v, self.pba.u, self.pba.du)
        
        # derivative of fluid continuity w.r.t. ALE displacement
        self.jac_pu = ufl.derivative(self.pbf.weakform_p, self.pba.u, self.pba.du)


    def set_forms_solver(self):
        pass


    def get_presolve_state(self):
        return False


    def assemble_residual_stiffness(self, t, subsolver=None):

        if bool(self.coupling_fluid_ale):
            # we need a vector representation of ufluid to apply in ALE DBCs
            if self.coupling_fluid_ale == 'strong_dirichlet':
                uf_vec = self.pbf.ti.update_uf_ost(self.pbf.v.vector, self.pbf.v_old.vector, self.pbf.uf_old.vector, ufl=False)
                self.ufa.vector.axpby(1.0, 0.0, uf_vec)
                self.ufa.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            if self.coupling_fluid_ale == 'weak_dirichlet':
                K_uv = fem.petsc.assemble_matrix(fem.form(self.jac_uv), self.pba.bc.dbcs)
                K_uv.assemble()
                K_list[2][0] = K_uv
        
        if bool(self.coupling_ale_fluid):
            # we need a vector representation of w to apply in fluid DBCs
            w_vec = self.pba.ti.update_w_ost(self.pba.u.vector, self.pba.u_old.vector, self.pba.w_old.vector, ufl=False)
            self.wf.vector.axpby(1.0, 0.0, w_vec)
            self.wf.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)

        r_list_fluid, K_list_fluid = self.pbf.assemble_residual_stiffness(t)
        
        r_list_ale, K_list_ale = self.pba.assemble_residual_stiffness(t)
        
        K_list = [[None]*3 for _ in range(3)]
        r_list = [None]*3
        
        K_list[0][0] = K_list_fluid[0][0]
        K_list[0][1] = K_list_fluid[0][1]
        
        # derivative of fluid momentum w.r.t. ALE velocity
        K_vu = fem.petsc.assemble_matrix(fem.form(self.jac_vu), self.pbf.bc.dbcs)
        K_vu.assemble()
        K_list[0][2] = K_vu

        K_list[1][0] = K_list_fluid[1][0]
        K_list[1][1] = K_list_fluid[1][1]
        
        # derivative of fluid continuity w.r.t. ALE velocity
        K_pu = fem.petsc.assemble_matrix(fem.form(self.jac_pu), [])
        K_pu.assemble()
        K_list[1][2] = K_pu

        K_list[2][2] = K_list_ale[0][0]

        r_list[0] = r_list_fluid[0]
        r_list[1] = r_list_fluid[1]
        r_list[2] = r_list_ale[0]
        
        return r_list, K_list


    # DEPRECATED: This is something we should actually not do! It will mess with gradients we need w.r.t. the reference (e.g. for FrSI)
    # Instead of moving the mesh, we formulate Navier-Stokes w.r.t. a reference state using the ALE kinematics
    def move_mesh(self):
        
        u = fem.Function(self.pba.Vcoord)
        u.interpolate(self.pba.u)
        self.io.mesh.geometry.x[:,:self.pba.dim] += u.x.array.reshape((-1, self.pba.dim))


    ### now the base routines for this problem
                
    def pre_timestep_routines(self):

        # perform Proper Orthogonal Decomposition
        if self.have_rom:
            self.rom.POD(self, self.pbf.V_v)


    def read_restart(self, sname, N):

        # fluid + ALE problem
        self.pbf.read_restart(sname, N)
        self.pba.read_restart(sname, N)


    def evaluate_initial(self):

        pass


    def write_output_ini(self):

        self.io.write_output(self, writemesh=True)


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

        self.io.write_output(self, N=N, t=t)

            
    def update(self):

        # update time step - fluid and ALE
        self.pbf.update()
        self.pba.update()
        
        if self.fluid_on_deformed=='mesh_move':
            self.move_mesh()


    def print_to_screen(self):

        self.pbf.print_to_screen()
        self.pba.print_to_screen()
    
    
    def induce_state_change(self):
        
        self.pbf.induce_state_change()
        self.pba.induce_state_change()


    def write_restart(self, sname, N):

        self.pbf.io.write_restart(self.pbf, N)
        self.pba.io.write_restart(self.pbf, N)
        
        
    def check_abort(self, t):
        
        self.pbf.check_abort(t)
        self.pba.check_abort(t)



class FluidmechanicsAleSolver(solver_base):

    def __init__(self, problem, solver_params):
    
        self.pb = problem
        
        self.solver_params = solver_params
        
        self.initialize_nonlinear_solver()


    def initialize_nonlinear_solver(self):
        
        # initialize nonlinear solver class
        self.solnln = solver_nonlin.solver_nonlinear(self.pb, solver_params=self.solver_params)


    def solve_initial_state(self):

        # consider consistent initial acceleration
        if self.pb.pbf.timint != 'static' and self.pb.pbf.restart_step == 0:
            # weak form at initial state for consistent initial acceleration solve
            weakform_a = self.pb.pbf.deltaW_kin_old + self.pb.pbf.deltaW_int_old - self.pb.pbf.deltaW_ext_old
            
            jac_a = ufl.derivative(weakform_a, self.pb.pbf.a_old, self.pb.pbf.dv) # actually linear in a_old

            # solve for consistent initial acceleration a_old
            self.solnln.solve_consistent_ini_acc(weakform_a, jac_a, self.pb.pbf.a_old)


    def solve_nonlinear_problem(self, t):
        
        self.solnln.newton(t)
        

    def print_timestep_info(self, N, t, wt):

        # print time step info to screen
        self.pb.pbf.ti.print_timestep(N, t, self.solnln.sepstring, wt=wt)
