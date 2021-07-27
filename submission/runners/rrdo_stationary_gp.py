# -*- coding: utf-8 -*-
"""
Stationary, i.e. one-shot, sampling.

Uses GP as model

Created on Sun Jun 14 15:17:20 2020
@author: Bogoclu
"""
import os
import pickle
import random
import string
import sys

import numpy as np

from pyRDO import ConditionalProbability, ConditionalMoment
from pyRDO import RRDO, UniVar, MultiVar, InputSpace, FullSpace
from pyRDO.doe.lhs import make_doe
from .inspyred_optimizer import InspyredOptimizer
from ..trainers.gpsklearn import fit_gpr


def direct_rrdo(objectives, constraints, trainer_args, num_samples, model_objectives, model_constraints, num_obj,
                num_con, n_inp_total, lower, upper, margs, ra_methods=None, sto_inps=None, opt_inps=None,
                scale_objs=False, obj_arg=None, con_arg=None, sto_obj_inds: list = None, sto_con_inds: list = None,
                obj_wgt=1.96, base_doe=True, target_fail_prob=None, pop_size=100, max_gens=100, punish_factor=100,
                pareto_size=1000, verbose=0, start_gen=None, res_key=None):

    dists = [UniVar(m["name"], **m["kwargs"]) for m in margs]
    mv = MultiVar(dists) # no correlation assumed
    if sto_inps is None:
        sto_inps = np.arange(len(mv))
    if opt_inps is None:
        opt_inps = np.arange(len(mv))
    if sto_obj_inds is None:
        sto_obj_inds = np.arange(num_obj)
    if sto_con_inds is None:
        sto_con_inds = np.arange(num_con)
    inp_space = InputSpace(mv, num_inp=n_inp_total,
                           opt_inps=opt_inps, sto_inps=sto_inps)

    lower_doe, upper_doe = inp_space.doe_bounds((1 - 0.999) / 2, lower, upper)
    print(lower_doe, upper_doe)
    if res_key is None:
        res_key = ''.join(
            random.choice(string.ascii_lowercase) for i in range(6))
    # Do here to avoid errors after computation
    res_key = str(res_key)

    cur_doe = make_doe(num_samples, lower_bound=lower_doe,
                       upper_bound=upper_doe)
    
    models = model_trainer(cur_doe, *trainer_args)
    if obj_arg is None:
        model_obj_arg = [models]
    else:
        try:
            model_obj_arg = list(obj_arg) + [models]
        except TypeError:
            obj_arg = [obj_arg]
            model_obj_arg = obj_arg + [models]
    if con_arg is None:
        model_con_arg = [models]
    else:
        try:
            model_con_arg = list(con_arg) + [models]
        except TypeError:
            con_arg = [con_arg]
            model_con_arg = con_arg + [models]

    full_space = FullSpace(inp_space, num_obj, num_con,
                           obj_fun=model_objectives, obj_arg=model_obj_arg,
                           con_fun=model_constraints, con_arg=model_con_arg,
                           sto_objs=sto_obj_inds, sto_cons=sto_con_inds)


    problem = make_problem(full_space, obj_wgt, target_fail_prob,
                           base_doe, ra_methods)
    def obj_con(x, *args, **kwargs):
        objs, feasible, det_cons, fail_probs = problem.obj_con(x, *args, **kwargs)
        if target_fail_prob is None:
            return objs, det_cons
        return objs, np.c_[det_cons, (target_fail_prob - fail_probs)/target_fail_prob]
    if obj_wgt is None:
        num_obj += len(sto_obj_inds)
    

    opter = InspyredOptimizer(obj_con, lower, upper, num_obj, method="NSGA",
                              verbose=verbose, scale_objs=scale_objs)
    res = opter.optimize(pop_size=pop_size, max_gens=max_gens,
                         punish_factor=punish_factor,
                         pareto_size=pareto_size, verbose=verbose,
                         start_gen=start_gen)

    cands = [np.array(r.candidate) for r in res]

    nit, nfev = opter.nit, opter.nfev

    results = []
    for i, c in enumerate(cands):
        print("Computing final result for pareto design", i + 1, "of", len(cands))
        proba_res, rob_res = problem.gen_post_proc(c)
        results.append({"proba": proba_res,
                        "rob": rob_res})
    save_res_pred = {"candidates": cands,
                     "results": results,
                     "num_opt_it": nit,
                     "num_opt_fev": nfev}

    if not res_key.endswith("_stat_gp_pred_res.pkl"):
        res_key += "_stat_gp_pred_res.pkl"
    with open(res_key, "wb") as f:
        pickle.dump(save_res_pred, f)

    full_space = FullSpace(inp_space, num_obj, num_con,
                           obj_fun=objectives, obj_arg=obj_arg,
                           con_fun=constraints, con_arg=con_arg,
                           sto_objs=sto_obj_inds, sto_cons=sto_con_inds)
    problem = make_problem(full_space, obj_wgt, target_fail_prob,
                           base_doe, ra_methods)
    res_key = res_key.replace("pred", "true")
    results = []
    for i, c in enumerate(cands):
        print("Computing final result for pareto design", i + 1, "of", len(cands))
        proba_res, rob_res = problem.gen_post_proc(c)
        results.append({"proba": proba_res,
                        "rob": rob_res})
    save_res_true = {"candidates": cands,
                     "results": results,
                     "doe": cur_doe,
                     "num_opt_it": nit,
                     "num_opt_fev": nfev}


    with open(res_key, "wb") as f:
        pickle.dump(save_res_true, f)

    return save_res_pred, save_res_true


def make_problem(full_space, obj_wgt, target_fail_prob, base_doe, ra_methods,
                 **kwargs):
    cmom = ConditionalMoment(full_space, obj_wgt=obj_wgt, base_doe=base_doe)
    cprob = None
    if target_fail_prob is not None:
        cprob = ConditionalProbability(target_fail_prob,
                                       len(full_space.con_inds["sto"]),
                                       call_args=kwargs,
                                       methods=ra_methods,
                                       )
    problem = RRDO(full_space, co_fp=cprob, co_mom=cmom)
    return problem

def model_trainer(doe, *functions):
    models = []
    for func in functions:
        output = func(doe)
        models.append(fit_gpr(doe, output))
    return models


def main(exname, save_dir="."):
    if exname == "ex1":
        from ..definitions.example1 import n_var, n_obj, n_con, target_pf, margs, lower, upper, n_stop, popsize, maxgens, ra_methods, scale_objs, obj_fun, con_fun, funs, model_obj, model_con
    elif exname == "ex2":
        from ..definitions.example2 import n_var, n_obj, n_con, target_pf, margs, lower, upper, n_stop, popsize, maxgens, ra_methods, scale_objs, obj_fun, con_fun
    elif exname == "ex3":
        from ..definitions.example3 import n_var, n_obj, n_con, target_pf, margs, lower, upper, n_stop, popsize, maxgens, ra_methods, scale_objs, obj_fun, con_fun
    else:
        raise ValueError(exname + " not recognized.")
    save_dir = os.path.join(save_dir, "results")
    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    try:
        res_key = sys.argv[1]
    except IndexError:
        res_key = None

    res_key = exname + (res_key if res_key is not None else "")
    res_key = os.path.join(save_dir, res_key)
    return direct_rrdo(obj_fun, con_fun, funs, n_stop, model_obj, model_con, n_obj, n_con, n_var, lower, upper, margs,
                       ra_methods=ra_methods, scale_objs=scale_objs, target_fail_prob=target_pf, pop_size=2 * popsize,
                       max_gens=2 * maxgens, verbose=1, res_key=res_key)

if __name__ == "__main__":
    _ = main("ex1")
