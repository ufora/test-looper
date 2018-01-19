import re

identifier_pattern = re.compile("[a-zA-Z0-9_-]+$")
substitution_pattern = re.compile(r"\$\{([a-zA-Z0-9_-]+)\}")

def variables_referenced(text):
    """Find all variables of the form ${varname}"""
    return set(substitution_pattern.findall(text))

def substitute_variables(text, vars):
    for varname, vardef in vars.iteritems():
        text = text.replace("${" + varname + "}", vardef)
    return text

def compute_graph_levels(deps, uses):
    """For every node in the dependency graph, compute levels.

    Level 0 = depends on nothing
    Level 1 = depends on only level 0

    returns a dictionary
        {level -> [var1, var2, ...]}
    """
    levels = {k: None for k in deps}

    def compute_level(var):
        if not deps[var]:
            return 0
        
        for d in deps[var]:
            if levels[d] is None:
                return None

        return max([levels[dep] for dep in deps[var]]) + 1

    dirty = set(deps)

    while dirty:
        new_dirty = set()
        
        for var in dirty:
            new_level = compute_level(var)
            if new_level != levels[var]:
                levels[var] = new_level
                for use in uses[var]:
                    new_dirty.add(use)

        dirty = new_dirty

    cycle = set(k for k in levels if levels[k] is None)
    if cycle:
        raise Exception("Cyclic variable dependencies detected: %s" % sorted(cycle))

    result = {l: [] for l in set(levels.values())}
    for dep, level in levels.items():
        result[level].append(dep)
    return result
   
def apply_variable_substitutions_and_merge(vardefs, extra_variables = {}):
    """Apply replacement logic to a set of variable definitions.

    extra_variables are additional substitutions to perform. They may
    not themselves refer to variables and will be applied at the end.

    Returns a final set of variables (with extra_variables merged in)
    """
    vardefs = dict(vardefs)

    deps = {}
    for var, vardef in vardefs.iteritems():
        if identifier_pattern.match(var):
            deps[var] = variables_referenced(vardef)

    #restrict the graph to variables we actually contain definitions for
    for var in deps:
        deps[var] = set([v for v in deps[var] if v in deps])

    #find all the places a variable is used
    uses = {}
    for var in deps:
        uses[var] = set()
    for var in deps:
        for var_used in deps[var]:
            uses[var_used].add(var)

    #place variables in levels
    levels = compute_graph_levels(deps, uses)

    for level, var_list in sorted(levels.items()):
        for var in var_list:
            for use in uses[var]:
                vardefs[use] = substitute_variables(vardefs[use], {var: vardefs[var]})

    for var in vardefs:
        vardefs[var] = substitute_variables(vardefs[var], extra_variables)

    for ev, ev_def in extra_variables.items():
        assert ev not in vardefs or vardefs[ev] == ev_def, "Can't define %s twice!" % ev
        vardefs[ev] = ev_def

    return vardefs

def apply_variable_substitutions_and_merge_repeatedly(vardefs, extra_variables = {}):
    """Repeatedly merge variables until we stabilize. This allows ${${A}${B}} to work. 

    The process is guaranteed to terminate because we are always consuming a level of 
    "$" indirections.
    """
    while True:
        new_vardefs = apply_variable_substitutions_and_merge(vardefs, extra_variables)
        if new_vardefs == vardefs:
            return new_vardefs
        else:
            vardefs = new_vardefs

