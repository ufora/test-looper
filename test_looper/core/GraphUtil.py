
def findAllNodes(nodes, edge_function):
    all_nodes = set()
    
    def walk(node):
        if node in all_nodes:
            return
        all_nodes.add(node)
        for child in edge_function(node):
            walk(child)

    for N in nodes:
        walk(N)

    return all_nodes

def placeNodesInLevels(nodes, edge_function):
    """Given a node "N" and an edge function, divide nodes into a sequence of groups
    so that nodes never flow into their own group or an ealier group. Throw
    if this has a cycle
    """
    all_nodes = findAllNodes(nodes, edge_function)

    reverse_edges = {c: set() for c in all_nodes}
    for node in all_nodes:
        for child in edge_function(node):
            reverse_edges[child].add(node)

    ready = [n for n in all_nodes if not reverse_edges[n]]
    for n in ready:
        del reverse_edges[n]
    groups = []

    while True:
        groups.append(ready)
        ready = []

        for n in groups[-1]:
            for child in edge_function(n):
                reverse_edges[child].remove(n)
                if not reverse_edges[child]:
                    ready.append(child)
                    del reverse_edges[child]

        if not ready:
            #we're done
            if reverse_edges:
                raise Exception("Cycle found in graph")
            return groups

def graphFindCycleMultipleRoots(roots, childrenFunction):
    """Find a cycle in the graph if it exists

    root - a starting node
    childrenFunction - a function from node to a list of children

    returns None, or a list of nodes in a cycle
    """
    not_circular = set()
    
    def check(node, above=()):
        if node in not_circular:
            return
        
        for child in childrenFunction(node):
            if child in above:
                return above + (child,)

            res = check(child, above + (node,))
            if res is not None:
                return res

        not_circular.add(node)

    for root in roots:
        res = check(root)
        if res:
            return res

def graphFindCycle(root, childrenFunction):
    """Find a cycle in the graph if it exists

    root - a starting node
    childrenFunction - a function from node to a list of children

    returns None, or a list of nodes in a cycle
    """
    return graphFindCycleMultipleRoots([root], childrenFunction)

def assertGraphHasNoCycles(root, childrenFunction):
    res = graphFindCycle(root, childrenFunction)
    if res:
        raise Exception("Circular dependencies: %s" % (res,))
