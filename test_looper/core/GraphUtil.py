
def graphFindCycle(root, childrenFunction):
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

    return check(root)

def assertGraphHasNoCycles(root, childrenFunction):
    res = graphFindCycle(root, childrenFunction)
    if res:
        raise Exception("Circular dependencies: %s" % res)
        