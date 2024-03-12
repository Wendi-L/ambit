#!/usr/bin/env python3

# Copyright (c) 2019-2024, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the MIT-style license found in the
# LICENSE file in the root directory of this source tree.

import sys, time
import numpy as np
from petsc4py import PETSc

from .. import utilities

"""
Ambit preconditioner classes

PETSc PC types:
https://www.mcs.anl.gov/petsc/petsc4py-current/docs/apiref/petsc4py.PETSc.PC.Type-class.html
https://petsc.org/main/petsc4py/petsc_python_types.html#petsc-python-preconditioner-type
"""

class block_precond():

    def __init__(self, iset, precond_fields, printenh, solparams, comm=None):

        self.iset = iset
        self.precond_fields = precond_fields
        self.nfields = len(precond_fields)
        assert(len(self.iset)==self.nfields)
        self.comm = comm
        # extra level of printing
        self.printenh = printenh
        # parameters
        self.solparams = solparams

        # type of scaling for approximation of Schur complement
        try: schur_block_scaling = self.solparams['schur_block_scaling']
        except: schur_block_scaling = [{'type' : 'diag', 'val' : 1.0}]*2

        if isinstance(schur_block_scaling, list):
            self.schur_block_scaling = schur_block_scaling
        else:
            self.schur_block_scaling = [schur_block_scaling]*2


    def create(self, pc):

        ts = time.time()
        utilities.print_status("Creating preconditioner objects...", self.comm, e=" ")

        # get reference to preconditioner matrix object
        _, self.P = pc.getOperators()

        self.tpy = None

        self.check_field_size()
        operator_mats = self.init_mat_vec(pc)

        self.ksp_fields, self.ksp_py_solver = [], [None]*self.nfields
        # create field ksps
        for n in range(self.nfields):
            self.ksp_fields.append( PETSc.KSP().create(self.comm) )
        # set the options
        for n in range(self.nfields):
            if self.precond_fields[n]['prec'] == 'amg':
                try: solvetype = self.precond_fields[n]['solve']
                except: solvetype = "preonly"
                self.ksp_fields[n].setType(solvetype)
                # GMRES or FGMES for inner solve
                if solvetype == 'gmres' or solvetype == 'fgmres':
                    try: maxiter = self.precond_fields[n]['maxiter']
                    except: maxiter = 1000
                    try: tolrel = self.precond_fields[n]['tolrel']
                    except: tolrel = 1e-5
                    try: tolabs = self.precond_fields[n]['tolabs']
                    except: tolabs = 1e-50
                    self.ksp_fields[n].setTolerances(rtol=tolrel, atol=tolabs, divtol=None, max_it=maxiter)
                try: amgtype = self.precond_fields[n]['amgtype']
                except: amgtype = "hypre"
                self.ksp_fields[n].getPC().setType(amgtype)
                if amgtype=="hypre":
                    self.ksp_fields[n].getPC().setHYPREType("boomeramg")
                # add PETSc options
                if 'petsc_options' in self.precond_fields[n].keys():
                    opt_dict = self.precond_fields[n]['petsc_options']
                    opts = PETSc.Options()
                    for o in opt_dict:
                        opts.setValue(o, opt_dict[o])
                    self.ksp_fields[n].getPC().setFromOptions()
                # print to view some settings...
                # print(self.ksp_fields[n].getPC().view())
                if solvetype == 'python':
                    try: niter = self.precond_fields[n]['stat_iter']
                    except: niter = 1
                    if self.precond_fields[n]['py_solver'] == "stat_iter_fixed":
                        self.ksp_py_solver[n] = stat_iter_fixed(niter)
                        self.ksp_fields[n].setPythonContext(self.ksp_py_solver[n])
                    elif self.precond_fields[n]['py_solver'] == "stat_iter_fixed_scr":
                        self.ksp_py_solver[n] = stat_iter_fixed_scr(niter, ksp_sub=self.ksp_fields[0])
                        self.ksp_fields[n].setPythonContext(self.ksp_py_solver[n])
                    else:
                        raise ValueError("Unknown Python solver option!")

            elif self.precond_fields[n]['prec'] == 'direct':
                self.ksp_fields[n].setType("preonly")
                self.ksp_fields[n].getPC().setType("lu")
                self.ksp_fields[n].getPC().setFactorSolverType("mumps")
            else:
                raise ValueError("Currently, only either 'amg' or 'direct' are supported as field-specific preconditioner.")

            # set operators and setup field prec
            self.ksp_fields[n].setOperators(operator_mats[n])
            #self.ksp_fields[n].getPC().setUp() # seems to break the solver when a direct prec is used! Needed???!

        te = time.time() - ts
        utilities.print_status("t = %.4f s" % (te), self.comm)


    def view(self, pc, vw):
        pass


    def setFromOptions(self, pc):
        pass


    def destroy(self, pc):
        pc.destroy()


class stat_iter_fixed():

    def __init__(self, niter):
        self.niter = niter
        raise RuntimeError("Experimental. You should not be here!")

    def create(self, ksp):
        pass

    def set_mat_vec(self, A):

        self.A = A


    def solve(self, ksp, x, y):

        Ayold = y.copy()
        yold = y.copy()
        wrk = y.copy()

        op, _ = ksp.getOperators()
        pc = ksp.getPC()

        A = op.mult
        P = pc.apply

        # stationary iteration rule:
        # y = y_old + P^{-1} (x - A y_old)
        # --> y += P^{-1} (x - A y_old)
        # --> y_old <- y
        for i in range(self.niter):

            A(yold, Ayold)     # A y_old
            P(x-Ayold, wrk)    # P^{-1} (x - A y_old)

            y.axpy(1., wrk)
            yold.axpby(1., 0., y)

        ksp.setConvergedReason(1)


class stat_iter_fixed_scr():

    def __init__(self, niter, ksp_sub=None):
        self.niter = niter
        self.ksp_sub = ksp_sub
        raise RuntimeError("Experimental. You should not be here!")

    def create(self, ksp):
        pass

    def set_mat_vec(self, A, C, B, Bt):

        self.A = A
        self.C = C
        self.B = B
        self.Bt = Bt

    def solve(self, ksp, x, y):

        yold = y.copy()
        wrk = y.copy()

        # ytilde = y.copy()
        ytilde = self.A.createVecLeft()

        op, _ = ksp.getOperators()
        pc = ksp.getPC()

        A = op.mult
        P = pc.apply

        # the sub-solve
        xdiff = y.copy()
        xhat = y.copy()

        for i in range(self.niter):

            self.C.mult(yold, xhat)

            xbar = self.Bt.createVecLeft()
            self.Bt.mult(yold, xbar)

            self.ksp_sub.solve(xbar, ytilde)

            xbar = self.B.createVecLeft()
            self.B.mult(ytilde, xbar)

            xdiff.axpby(1., 0., xhat)
            xdiff.axpy(-1., xbar)

            P(x-xdiff, wrk)

            y.axpy(1., wrk)
            yold.axpby(1., 0., y)

        ksp.setConvergedReason(1)





# Schur complement preconditioner (using a modified diag(A)^{-1} instead of A^{-1} in the Schur complement)
class schur_2x2(block_precond):

    def check_field_size(self):
        assert(self.nfields==2)


    def init_mat_vec(self, pc):

        self.A  = self.P.createSubMatrix(self.iset[0],self.iset[0])
        self.Bt = self.P.createSubMatrix(self.iset[0],self.iset[1])
        self.B  = self.P.createSubMatrix(self.iset[1],self.iset[0])
        self.C  = self.P.createSubMatrix(self.iset[1],self.iset[1])

        # the matrix to later insert the diagonal
        self.Adinv = PETSc.Mat().createAIJ(self.A.getSizes(), bsize=None, nnz=(1,1), csr=None, comm=self.comm)
        self.Adinv.setUp()
        self.Adinv.assemble()
        # set 1's to get correct allocation pattern
        self.Adinv.shift(1.)

        if self.schur_block_scaling[0]['type']=='diag':
            self.adinv_vec = self.A.getDiagonal()
        elif self.schur_block_scaling[0]['type']=='rowsum':
            self.adinv_vec = self.A.getRowSum()
        elif self.schur_block_scaling[0]['type']=='none':
            self.adinv_vec = self.A.createVecLeft()
            self.adinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        self.Smod = self.C.copy(structure=PETSc.Mat.Structure.DIFFERENT_NONZERO_PATTERN)

        self.Adinv_Bt = self.Adinv.matMult(self.Bt)
        self.B_Adinv_Bt = self.B.matMult(self.Adinv_Bt)

        # need to set Smod here to get the data structures right
        self.Smod.axpy(-1., self.B_Adinv_Bt)

        self.x1, self.x2 = self.A.createVecLeft(), self.Smod.createVecLeft()
        self.y1, self.y2 = self.A.createVecLeft(), self.Smod.createVecLeft()
        self.z1, self.z2 = self.A.createVecLeft(), self.Smod.createVecLeft()

        self.By1 = PETSc.Vec().createMPI(size=(self.B.getLocalSize()[0],self.B.getSize()[0]), comm=self.comm)
        self.Bty2 = PETSc.Vec().createMPI(size=(self.Bt.getLocalSize()[0],self.Bt.getSize()[0]), comm=self.comm)

        # do we need these???
        self.A.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)
        self.Smod.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)

        return [self.A, self.Smod]


    def setUp(self, pc):

        ts = time.time()

        self.P.createSubMatrix(self.iset[0],self.iset[0], submat=self.A)
        self.P.createSubMatrix(self.iset[0],self.iset[1], submat=self.Bt)
        self.P.createSubMatrix(self.iset[1],self.iset[0], submat=self.B)
        self.P.createSubMatrix(self.iset[1],self.iset[1], submat=self.C)

        if self.schur_block_scaling[0]['type']=='diag':
            self.A.getDiagonal(result=self.adinv_vec)
            self.adinv_vec.reciprocal()
        elif self.schur_block_scaling[0]['type']=='rowsum':
            self.A.getRowSum(result=self.adinv_vec)
            self.adinv_vec.abs()
            self.adinv_vec.reciprocal()
        elif self.schur_block_scaling[0]['type']=='none':
            self.adinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        self.adinv_vec.scale(self.schur_block_scaling[0]['val'])

        # form diag(A)^{-1}
        self.Adinv.setDiagonal(self.adinv_vec, addv=PETSc.InsertMode.INSERT)

        self.Adinv.matMult(self.Bt, result=self.Adinv_Bt)      # diag(A)^{-1} Bt
        self.B.matMult(self.Adinv_Bt, result=self.B_Adinv_Bt)  # B diag(A)^{-1} Bt

        # --- modified Schur complement Smod = C - B diag(A)^{-1} Bt
        # compute self.Smod = self.C - B_Adinv_Bt
        self.C.copy(result=self.Smod)
        self.Smod.axpy(-1., self.B_Adinv_Bt)

        # operator values have changed - do we need to re-set them?
        self.ksp_fields[0].setOperators(self.A)
        self.ksp_fields[1].setOperators(self.Smod)

        # Schur complement reduction
        if self.ksp_py_solver[1] is not None:
            if self.precond_fields[1]['py_solver']=='stat_iter_fixed_scr':
                self.ksp_py_solver[1].set_mat_vec(self.A, self.C, self.B, self.Bt)

        te = time.time() - ts
        if self.printenh:
            utilities.print_status("       === PREC setup, te = %.4f s" % (te), self.comm)


    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors
        x.getSubVector(self.iset[0], subvec=self.x1)
        x.getSubVector(self.iset[1], subvec=self.x2)

        # 1) solve A * y_1 = x_1
        self.ksp_fields[0].solve(self.x1, self.y1)

        self.B.mult(self.y1, self.By1)

        # compute z2 = x2 - self.By1
        self.z2.axpby(1., 0., self.x2)
        self.z2.axpy(-1., self.By1)

        # 2) solve Smod * y_2 = z_2
        self.ksp_fields[1].solve(self.z2, self.y2)

        self.Bt.mult(self.y2, self.Bty2)

        # compute z1 = x1 - self.Bty2
        self.z1.axpby(1., 0., self.x1)
        self.z1.axpy(-1., self.Bty2)

        # 3) solve A * y_1 = z_1
        self.ksp_fields[0].solve(self.z1, self.y1)

        # restore/clean up
        x.restoreSubVector(self.iset[0], subvec=self.x1)
        x.restoreSubVector(self.iset[1], subvec=self.x2)

        # set into y vector
        y.setValues(self.iset[0], self.y1.array)
        y.setValues(self.iset[1], self.y2.array)

        y.assemble()



# special MH Schur complement 3x3 preconditioner
class schur_3x3(block_precond):

    def check_field_size(self):
        assert(self.nfields==3)


    def init_mat_vec(self, pc):

        self.A  = self.P.createSubMatrix(self.iset[0],self.iset[0])
        self.Bt = self.P.createSubMatrix(self.iset[0],self.iset[1])
        self.Dt = self.P.createSubMatrix(self.iset[0],self.iset[2])
        self.B  = self.P.createSubMatrix(self.iset[1],self.iset[0])
        self.C  = self.P.createSubMatrix(self.iset[1],self.iset[1])
        self.Et = self.P.createSubMatrix(self.iset[1],self.iset[2])
        self.D  = self.P.createSubMatrix(self.iset[2],self.iset[0])
        self.E  = self.P.createSubMatrix(self.iset[2],self.iset[1])
        self.R  = self.P.createSubMatrix(self.iset[2],self.iset[2])

        # the matrix to later insert the diagonal
        self.Adinv = PETSc.Mat().createAIJ(self.A.getSizes(), bsize=None, nnz=(1,1), csr=None, comm=self.comm)
        self.Adinv.setUp()
        self.Adinv.assemble()
        # set 1's to get correct allocation pattern
        self.Adinv.shift(1.)

        if self.schur_block_scaling[0]['type']=='diag':
            self.adinv_vec = self.A.getDiagonal()
        elif self.schur_block_scaling[0]['type']=='rowsum':
            self.adinv_vec = self.A.getRowSum()
        elif self.schur_block_scaling[0]['type']=='none':
            self.adinv_vec = self.A.createVecLeft()
            self.adinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        self.Smod = self.C.copy(structure=PETSc.Mat.Structure.DIFFERENT_NONZERO_PATTERN)

        if self.schur_block_scaling[1]['type']=='diag':
            self.smoddinv_vec = self.Smod.getDiagonal()
        elif self.schur_block_scaling[1]['type']=='rowsum':
            self.smoddinv_vec = self.Smod.getRowSum()
        elif self.schur_block_scaling[1]['type']=='none':
            self.smoddinv_vec = self.Smod.createVecLeft()
            self.smoddinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        # the matrix to later insert the diagonal
        self.Smoddinv = PETSc.Mat().createAIJ(self.C.getSizes(), bsize=None, nnz=(1,1), csr=None, comm=self.comm)
        self.Smoddinv.setUp()
        self.Smoddinv.assemble()
        # set 1's to get correct allocation pattern
        self.Smoddinv.shift(1.)

        self.Tmod = self.Et.copy(structure=PETSc.Mat.Structure.DIFFERENT_NONZERO_PATTERN)
        self.Wmod = self.R.copy(structure=PETSc.Mat.Structure.DIFFERENT_NONZERO_PATTERN)

        self.Adinv_Bt = self.Adinv.matMult(self.Bt)
        self.DBt = self.D.matMult(self.Adinv_Bt)

        self.B_Adinv_Bt = self.B.matMult(self.Adinv_Bt)

        self.Adinv_Dt = self.Adinv.matMult(self.Dt)
        self.B_Adinv_Dt = self.B.matMult(self.Adinv_Dt)

        self.D_Adinv_Dt = self.D.matMult(self.Adinv_Dt)

        # need to set Smod and Tmod here to get the data structures right
        self.Smod.axpy(-1., self.B_Adinv_Bt)
        self.Tmod.axpy(-1., self.B_Adinv_Dt)

        self.Smoddinv_Tmod = self.Smoddinv.matMult(self.Tmod)

        self.Bt_Smoddinv_Tmod = self.Bt.matMult(self.Smoddinv_Tmod)

        self.Adinv_Bt_Smoddinv_Tmod = self.Adinv.matMult(self.Bt_Smoddinv_Tmod)
        self.D_Adinv_Bt_Smoddinv_Tmod = self.D.matMult(self.Adinv_Bt_Smoddinv_Tmod)

        self.E_Smoddinv_Tmod = self.E.matMult(self.Smoddinv_Tmod)

        self.By1 = PETSc.Vec().createMPI(size=(self.B.getLocalSize()[0],self.B.getSize()[0]), comm=self.comm)
        self.Dy1 = PETSc.Vec().createMPI(size=(self.D.getLocalSize()[0],self.D.getSize()[0]), comm=self.comm)
        self.DBty2 = PETSc.Vec().createMPI(size=(self.DBt.getLocalSize()[0],self.DBt.getSize()[0]), comm=self.comm)
        self.Ey2 = PETSc.Vec().createMPI(size=(self.E.getLocalSize()[0],self.E.getSize()[0]), comm=self.comm)
        self.Tmody3 = PETSc.Vec().createMPI(size=(self.Et.getLocalSize()[0],self.Et.getSize()[0]), comm=self.comm)
        self.Bty2 = PETSc.Vec().createMPI(size=(self.Bt.getLocalSize()[0],self.Bt.getSize()[0]), comm=self.comm)
        self.Dty3 = PETSc.Vec().createMPI(size=(self.Dt.getLocalSize()[0],self.Dt.getSize()[0]), comm=self.comm)

        self.x1, self.x2, self.x3 = self.A.createVecLeft(), self.Smod.createVecLeft(), self.Wmod.createVecLeft()
        self.y1, self.y2, self.y3 = self.A.createVecLeft(), self.Smod.createVecLeft(), self.Wmod.createVecLeft()
        self.z1, self.z2, self.z3 = self.A.createVecLeft(), self.Smod.createVecLeft(), self.Wmod.createVecLeft()

        # do we need these???
        self.A.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)
        self.Smod.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)
        self.Wmod.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)

        return [self.A, self.Smod, self.Wmod]


    def setUp(self, pc):

        ts = time.time()

        self.P.createSubMatrix(self.iset[0],self.iset[0], submat=self.A)
        self.P.createSubMatrix(self.iset[0],self.iset[1], submat=self.Bt)
        self.P.createSubMatrix(self.iset[0],self.iset[2], submat=self.Dt)
        self.P.createSubMatrix(self.iset[1],self.iset[0], submat=self.B)
        self.P.createSubMatrix(self.iset[1],self.iset[1], submat=self.C)
        self.P.createSubMatrix(self.iset[1],self.iset[2], submat=self.Et)
        self.P.createSubMatrix(self.iset[2],self.iset[0], submat=self.D)
        self.P.createSubMatrix(self.iset[2],self.iset[1], submat=self.E)
        self.P.createSubMatrix(self.iset[2],self.iset[2], submat=self.R)

        if self.schur_block_scaling[0]['type']=='diag':
            self.A.getDiagonal(result=self.adinv_vec)
            self.adinv_vec.reciprocal()
        elif self.schur_block_scaling[0]['type']=='rowsum':
            self.A.getRowSum(result=self.adinv_vec)
            self.adinv_vec.abs()
            self.adinv_vec.reciprocal()
        elif self.schur_block_scaling[0]['type']=='none':
            self.adinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        self.adinv_vec.scale(self.schur_block_scaling[0]['val'])

        # form diag(A)^{-1}
        self.Adinv.setDiagonal(self.adinv_vec, addv=PETSc.InsertMode.INSERT)

        self.Adinv.matMult(self.Bt, result=self.Adinv_Bt)      # diag(A)^{-1} Bt
        self.B.matMult(self.Adinv_Bt, result=self.B_Adinv_Bt)  # B diag(A)^{-1} Bt

        # --- modified Schur complement Smod = C - B diag(A)^{-1} Bt
        # compute self.Smod = self.C - B_Adinv_Bt
        self.C.copy(result=self.Smod)
        self.Smod.axpy(-1., self.B_Adinv_Bt)

        # --- Tmod = Et - B diag(A)^{-1} Dt

        self.Adinv.matMult(self.Dt, result=self.Adinv_Dt)      # diag(A)^{-1} Dt
        self.B.matMult(self.Adinv_Dt, result=self.B_Adinv_Dt)  # B diag(A)^{-1} Dt

        # compute self.Tmod = self.Et - B_Adinv_Dt
        self.Et.copy(result=self.Tmod)
        self.Tmod.axpy(-1., self.B_Adinv_Dt)

        # --- Wmod = R - D diag(A)^{-1} Dt - E diag(Smod)^{-1} Tmod + D diag(A)^{-1} Bt diag(Smod)^{-1} Tmod

        if self.schur_block_scaling[1]['type']=='diag':
            self.Smod.getDiagonal(result=self.smoddinv_vec)
            self.smoddinv_vec.reciprocal()
        elif self.schur_block_scaling[1]['type']=='rowsum':
            self.Smod.getRowSum(result=self.smoddinv_vec)
            self.smoddinv_vec.abs()
            self.smoddinv_vec.reciprocal()
        elif self.schur_block_scaling[1]['type']=='none':
            self.smoddinv_vec.set(1.0)
        else:
            raise ValueError("Unknown schur_block_scaling option!")

        self.smoddinv_vec.scale(self.schur_block_scaling[1]['val'])

        # form diag(Smod)^{-1}
        self.Smoddinv.setDiagonal(self.smoddinv_vec, addv=PETSc.InsertMode.INSERT)

        self.Smoddinv.matMult(self.Tmod, result=self.Smoddinv_Tmod)                        # diag(Smod)^{-1} Tmod

        self.Bt.matMult(self.Smoddinv_Tmod, result=self.Bt_Smoddinv_Tmod)                  # Bt diag(Smod)^{-1} Tmod

        self.Adinv.matMult(self.Bt_Smoddinv_Tmod, result=self.Adinv_Bt_Smoddinv_Tmod)      # diag(A)^{-1} ( Bt diag(Smod)^{-1} Tmod )

        self.D.matMult(self.Adinv_Bt_Smoddinv_Tmod, result=self.D_Adinv_Bt_Smoddinv_Tmod)  # D diag(A)^{-1} ( Bt diag(Smod)^{-1} Tmod )

        self.D.matMult(self.Adinv_Bt, result=self.DBt)                                     # D diag(A)^{-1} Bt for later use

        self.E.matMult(self.Smoddinv_Tmod, result=self.E_Smoddinv_Tmod)                    # E diag(Smod)^{-1} Tmod

        self.D.matMult(self.Adinv_Dt, result=self.D_Adinv_Dt)                              # D diag(A)^{-1} Dt

        # compute self.Wmod = self.R - D_Adinv_Dt - E_Smoddinv_Tmod + D_Adinv_Bt_Smoddinv_Tmod
        self.R.copy(result=self.Wmod)
        self.Wmod.axpy(-1., self.D_Adinv_Dt)
        self.Wmod.axpy(-1., self.E_Smoddinv_Tmod)
        self.Wmod.axpy(1., self.D_Adinv_Bt_Smoddinv_Tmod)

        # operator values have changed - do we need to re-set them?
        self.ksp_fields[0].setOperators(self.A)
        self.ksp_fields[1].setOperators(self.Smod)
        self.ksp_fields[2].setOperators(self.Wmod)

        # Schur complement reduction
        if self.ksp_py_solver[1] is not None:
            if self.precond_fields[1]['py_solver']=='stat_iter_fixed_scr':
                self.ksp_py_solver[1].set_mat_vec(self.A, self.C, self.B, self.Bt)

        te = time.time() - ts
        if self.printenh:
            utilities.print_status("       === PREC setup, te = %.4f s" % (te), self.comm)


    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors (references!)
        x.getSubVector(self.iset[0], subvec=self.x1)
        x.getSubVector(self.iset[1], subvec=self.x2)
        x.getSubVector(self.iset[2], subvec=self.x3)

        tss = time.time()

        # 1) solve A * y_1 = x_1
        self.ksp_fields[0].solve(self.x1, self.y1)

        self.B.mult(self.y1, self.By1)

        # compute z2 = x2 - self.By1
        self.z2.axpby(1., 0., self.x2)
        self.z2.axpy(-1., self.By1)

        # 2) solve Smod * y_2 = z_2
        self.ksp_fields[1].solve(self.z2, self.y2)

        self.D.mult(self.y1, self.Dy1)
        self.DBt.mult(self.y2, self.DBty2)
        self.E.mult(self.y2, self.Ey2)

        # compute z3 = x3 - (self.Dy1 - self.DBty2 + self.Ey2)
        self.z3.axpby(1., 0., self.x3)
        self.z3.axpy(-1., self.Dy1)
        self.z3.axpy(1., self.DBty2)
        self.z3.axpy(-1., self.Ey2)

        # 3) solve Wmod * y_3 = z_3
        self.ksp_fields[2].solve(self.z3, self.y3)

        self.Tmod.mult(self.y3, self.Tmody3)

        # compute z2 = x2 - self.By1 - self.Tmody3
        self.z2.axpby(1., 0., self.x2)
        self.z2.axpy(-1., self.By1)
        self.z2.axpy(-1., self.Tmody3)

        # 4) solve Smod * y_2 = z_2
        self.ksp_fields[1].solve(self.z2, self.y2)

        self.Bt.mult(self.y2, self.Bty2)
        self.Dt.mult(self.y3, self.Dty3)

        # compute z1 = x1 - self.Bty2 - self.Dty3
        self.z1.axpby(1., 0., self.x1)
        self.z1.axpy(-1., self.Bty2)
        self.z1.axpy(-1., self.Dty3)

        # 5) solve A * y_1 = z_1
        self.ksp_fields[0].solve(self.z1, self.y1)

        # restore/clean up
        x.restoreSubVector(self.iset[0], subvec=self.x1)
        x.restoreSubVector(self.iset[1], subvec=self.x2)
        x.restoreSubVector(self.iset[2], subvec=self.x3)

        # set into y vector
        y.setValues(self.iset[0], self.y1.array)
        y.setValues(self.iset[1], self.y2.array)
        y.setValues(self.iset[2], self.y3.array)

        y.assemble()



# symmetric BGS with inner schur_3x3 (tailored towards monolithic FrSI, where the 4th block is the ALE problem)
# influence ALE on fluid is much more relevant than vice versa: in BGS, solve ALE first and then do 3x3 solve for fluid
class schur_bgssym_4x4(schur_3x3):

    def check_field_size(self):
        assert(self.nfields==4)


    def init_mat_vec(self, pc):
        opmats = super().init_mat_vec(pc)

        self.G  = self.P.createSubMatrix(self.iset[3],self.iset[3])

        # create index set encompassing the first 3 blocks
        self.iset_012 = self.iset[0].expand(self.iset[1])
        self.iset_012 = self.iset_012.expand(self.iset[2])
        self.iset_012.sort()
        # get additional offdiagonal block
        self.Ft = self.P.createSubMatrix(self.iset_012,self.iset[3])
        self.F  = self.P.createSubMatrix(self.iset[3],self.iset_012)

        self.x4 = self.G.createVecLeft()
        self.y4 = self.G.createVecLeft()
        self.z4 = self.G.createVecLeft()

        self.x123 = self.Ft.createVecLeft()
        self.y123 = self.Ft.createVecLeft()
        self.z123 = self.Ft.createVecLeft()

        self.Fty4 = PETSc.Vec().createMPI(size=(self.Ft.getLocalSize()[0],self.Ft.getSize()[0]), comm=self.comm)
        self.Fy123 = PETSc.Vec().createMPI(size=(self.F.getLocalSize()[0],self.F.getSize()[0]), comm=self.comm)

        self.xtmp = PETSc.Vec().createMPI(size=(self.P.getLocalSize()[0],self.P.getSize()[0]), comm=self.comm)

        # do we need this???
        self.G.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)

        return [opmats[0], opmats[1], opmats[2], self.G]


    def setUp(self, pc):
        super().setUp(pc)

        self.P.createSubMatrix(self.iset[3],self.iset[3], submat=self.G)
        self.P.createSubMatrix(self.iset_012,self.iset[3], submat=self.Ft)
        self.P.createSubMatrix(self.iset[3],self.iset_012, submat=self.F)

        # operator values have changed - do we need to re-set it?
        self.ksp_fields[3].setOperators(self.G)


    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors
        x.getSubVector(self.iset[3], subvec=self.x4)
        x.getSubVector(self.iset_012, subvec=self.x123)

        # 1) solve G * y_4 = x_4
        self.ksp_fields[3].solve(self.x4, self.y4)

        self.Ft.mult(self.y4, self.Fty4)

        # compute z123 = x123 - self.Fty4
        self.z123.axpby(1., 0., self.x123)
        self.z123.axpy(-1., self.Fty4)

        # 2) Schur solve A * y_123 = z_123
        self.xtmp.setValues(self.iset_012, self.z123.array)
        self.xtmp.assemble()
        super().apply(pc, self.xtmp, y)

        y.getSubVector(self.iset_012, subvec=self.y123)

        self.F.mult(self.y123, self.Fy123)

        # compute z4 = x4 - self.Fy123
        self.z4.axpby(1., 0., self.x4)
        self.z4.axpy(-1., self.Fy123)

        # 3) solve G * y_4 = z_4
        self.ksp_fields[3].solve(self.z4, self.y4)

        # restore/clean up
        y.restoreSubVector(self.iset_012, subvec=self.y123)
        x.restoreSubVector(self.iset_012, subvec=self.x123)
        x.restoreSubVector(self.iset[3], subvec=self.x4)

        # set into y vector
        y.setValues(self.iset[3], self.y4.array)

        y.assemble()


class schur_bgs_4x4(schur_bgssym_4x4):

    def setUp(self, pc):
        super().setUp(pc)

        self.P.createSubMatrix(self.iset[3],self.iset[3], submat=self.G)
        self.P.createSubMatrix(self.iset_012,self.iset[3], submat=self.Ft)

        # operator values have changed - do we need to re-set it?
        self.ksp_fields[3].setOperators(self.G)

    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors
        x.getSubVector(self.iset[3], subvec=self.x4)
        x.getSubVector(self.iset_012, subvec=self.x123)

        # 1) solve G * y_4 = x_4
        self.ksp_fields[3].solve(self.x4, self.y4)

        self.Ft.mult(self.y4, self.Fty4)

        # compute z123 = x123 - self.Fty4
        self.z123.axpby(1., 0., self.x123)
        self.z123.axpy(-1., self.Fty4)

        # 2) Schur solve A * y_123 = z_123
        self.xtmp.setValues(self.iset_012, self.z123.array)
        self.xtmp.assemble()
        schur_3x3.apply(self, pc, self.xtmp, y)

        # restore/clean up
        x.restoreSubVector(self.iset_012, subvec=self.x123)
        x.restoreSubVector(self.iset[3], subvec=self.x4)

        # set into y vector
        y.setValues(self.iset[3], self.y4.array)

        y.assemble()


# Schur complement preconditioner replacing the last solve with a diag(A)^{-1} update
class simple_2x2(schur_2x2):

    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors
        x.getSubVector(self.iset[0], subvec=self.x1)
        x.getSubVector(self.iset[1], subvec=self.x2)

        # 1) solve A * y_1 = x_1
        self.ksp_fields[0].solve(self.x1, self.y1)

        self.B.mult(self.y1, self.By1)

        # compute z2 = x2 - self.By1
        self.z2.axpby(1., 0., self.x2)
        self.z2.axpy(-1., self.By1)

        # 2) solve Smod * y_2 = z_2
        self.ksp_fields[1].solve(self.z2, self.y2)

        # 3) update y_1
        self.Adinv_Bt.mult(self.y2, self.Bty2)
        # compute y1 -= self.Bty2
        self.y1.axpy(-1., self.Bty2)

        # restore/clean up
        x.restoreSubVector(self.iset[0], subvec=self.x1)
        x.restoreSubVector(self.iset[1], subvec=self.x2)

        # set into y vector
        y.setValues(self.iset[0], self.y1.array)
        y.setValues(self.iset[1], self.y2.array)

        y.assemble()



# own 2x2 Block Gauss-Seidel (can be also called via PETSc's fieldsplit) - implementation mainly for testing purposes

# P = [A  0] [I  0] [I  0], --> P^{-1} = [I    0   ] [ I  0] [A^{-1} 0]
#     [0  I] [B  I] [0  C]               [0  C^{-1}] [-B  I] [0      I]
class bgs_2x2(block_precond):

    def check_field_size(self):
        assert(self.nfields==2)


    def init_mat_vec(self, pc):

        self.A  = self.P.createSubMatrix(self.iset[0],self.iset[0])
        self.Bt = self.P.createSubMatrix(self.iset[0],self.iset[1])
        self.B  = self.P.createSubMatrix(self.iset[1],self.iset[0])
        self.C  = self.P.createSubMatrix(self.iset[1],self.iset[1])

        self.By1 = PETSc.Vec().createMPI(size=(self.B.getLocalSize()[0],self.B.getSize()[0]), comm=self.comm)
        self.Bty2 = PETSc.Vec().createMPI(size=(self.Bt.getLocalSize()[0],self.Bt.getSize()[0]), comm=self.comm)

        self.x1, self.x2 = self.A.createVecLeft(), self.C.createVecLeft()
        self.y1, self.y2 = self.A.createVecLeft(), self.C.createVecLeft()
        self.z2 = self.C.createVecLeft()

        # do we need these???
        self.A.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)
        self.C.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)

        return [self.A, self.C]


    def setUp(self, pc):

        ts = time.time()

        self.P.createSubMatrix(self.iset[0],self.iset[0], submat=self.A)
        self.P.createSubMatrix(self.iset[0],self.iset[1], submat=self.Bt)
        self.P.createSubMatrix(self.iset[1],self.iset[0], submat=self.B)
        self.P.createSubMatrix(self.iset[1],self.iset[1], submat=self.C)

        # operator values have changed - do we need to re-set them?
        self.ksp_fields[0].setOperators(self.A)
        self.ksp_fields[1].setOperators(self.C)

        te = time.time() - ts
        if self.printenh:
            utilities.print_status("       === PREC setup, te = %.4f s" % (te), self.comm)


    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors
        x.getSubVector(self.iset[0], subvec=self.x1)
        x.getSubVector(self.iset[1], subvec=self.x2)

        # 1) solve A * y_1 = x_1
        self.ksp_fields[0].solve(self.x1, self.y1)

        self.B.mult(self.y1, self.By1)

        # compute z2 = x2 - self.By1
        self.z2.axpby(1., 0., self.x2)
        self.z2.axpy(-1., self.By1)

        # 2) solve C * y_2 = z_2
        self.ksp_fields[1].solve(self.z2, self.y2)

        # restore/clean up
        x.restoreSubVector(self.iset[0], subvec=self.x1)
        x.restoreSubVector(self.iset[1], subvec=self.x2)

        # set into y vector
        y.setValues(self.iset[0], self.y1.array)
        y.setValues(self.iset[1], self.y2.array)

        y.assemble()



# own 2x2 Jacobi (can be also called via PETSc's fieldsplit) - implementation mainly for testing purposes
# P = [A  0], --> P^{-1} = [A^{-1}  0  ]
#     [0  C]               [  0  C^{-1}]
class jacobi_2x2(block_precond):

    def check_field_size(self):
        assert(self.nfields==2)


    def init_mat_vec(self, pc):

        self.A  = self.P.createSubMatrix(self.iset[0],self.iset[0])
        self.C  = self.P.createSubMatrix(self.iset[1],self.iset[1])

        # do we need these???
        self.A.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)
        self.C.setOption(PETSc.Mat.Option.NO_OFF_PROC_ZERO_ROWS, True)

        return [self.A, self.C]


    def setUp(self, pc):

        ts = time.time()

        self.P.createSubMatrix(self.iset[0],self.iset[0], submat=self.A)
        self.P.createSubMatrix(self.iset[1],self.iset[1], submat=self.C)

        # operator values have changed - do we need to re-set them?
        self.ksp_fields[0].setOperators(self.A)
        self.ksp_fields[1].setOperators(self.C)

        te = time.time() - ts
        if self.printenh:
            utilities.print_status("       === PREC setup, te = %.4f s" % (te), self.comm)


    # computes y = P^{-1} x
    def apply(self, pc, x, y):

        # get subvectors (references!)
        x.getSubVector(self.iset[0], subvec=self.x1)
        x.getSubVector(self.iset[1], subvec=self.x2)

        # 1) solve A * y_1 = x_1
        self.ksp_fields[0].solve(self.x1, self.y1)

        # 2) solve C * y_2 = x_2
        self.ksp_fields[1].solve(self.x2, self.y2)

        # restore/clean up
        x.restoreSubVector(self.iset[0], subvec=self.x1)
        x.restoreSubVector(self.iset[1], subvec=self.x2)

        # set into y vector
        y.setValues(self.iset[0], self.y1.array)
        y.setValues(self.iset[1], self.y2.array)

        y.assemble()
