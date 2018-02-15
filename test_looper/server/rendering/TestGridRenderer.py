import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer

class TestGridRenderer:
    """Describes a mechanism for grouping tests into columns and rows along with state to filter and expand."""
    def __init__(self, tests_in_rows, row_ordering, column_expansions = None):
        if column_expansions is None:
            #by default, expand the first - of the environment
            column_expansions = {(): {"type":"env", "prefix": 0}}

        self.row_ordering = row_ordering
        self.columnExpansions = column_expansions
        self.column_children = {}
        self.leafColumnAllTests = {}
        self.tests_in_rows_and_columns = {k: self.breakTestsIntoColumns(v) for k,v in tests_in_rows.iteritems()}
        self.column_widths = {}

        self.computeColumnWidths()

    def computeColumnWidths(self):
        def compute(col):
            if col not in self.column_children:
                return 1
            res = 0
            for child in self.column_children[col]:
                res += compute(col + (child,))

            self.column_widths[col] = res

            return res

        compute(())

    def breakTestsIntoColumns(self, tests):
        row = {}

        for t in tests:
            col = self.testGetColumn(t)
            if col not in row:
                row[col] = []
            row[col].append(t)

            if col not in self.leafColumnAllTests:
                self.leafColumnAllTests[col] = []
            self.leafColumnAllTests[col].append(t)

        return row

    def testGetColumn(self, t):
        curColumn = ()

        while curColumn in self.columnExpansions:
            expansion = self.columnExpansions[curColumn]
            group = self.applyExpansion(t, expansion)

            if curColumn not in self.column_children:
                self.column_children[curColumn] = set()
            self.column_children[curColumn].add(group)

            curColumn = curColumn + (group,)

        return curColumn

    def envNameForTest(self, test):
        return test.testDefinition.environment.environment_name.split("/")[-1]
    
    def applyExpansion(self, test, expansion):
        if expansion["type"] == "env":
            name = self.envNameForTest(test)

            name = name.split("-")
            if expansion["prefix"] > len(name):
                return None
            else:
                return name[expansion["prefix"]]
        return None

    def columnsInOrder(self):
        columns = []
        def walk(col):
            if col not in self.column_children:
                columns.append(col)
            else:
                for child in sorted(self.column_children[col]):
                    walk(col + (child,))
        walk(())

        return columns

    def getGridHeaders(self, url_fun):
        header_meaning = [[()]]

        while [h for h in header_meaning[-1] if h is not None]:
            new_header_meaning = []
            for h in header_meaning[-1]:
                if h is None:
                    new_header_meaning.append(None)
                elif h in self.column_children:
                    for child in self.column_children[h]:
                        new_header_meaning.append(h + (child,))
                else:
                    new_header_meaning.append(None)

            header_meaning.append(new_header_meaning)

        def cellForHeader(group):
            if group is None:
                return ""
            if group not in self.column_children:
                return self.groupHeader(group, url_fun)

            return {"content": self.groupHeader(group, url_fun), "colspan": self.column_widths[group]}

        return [[cellForHeader(c) for c in line] for line in header_meaning[1:-1]] or [[]]

    def groupHeader(self, group, url_fun):
        if group not in self.leafColumnAllTests:
            canExpand = False
            canCollapse = True
        else:
            canCollapse = False
            canExpand = len(set(self.envNameForTest(t) for t in self.leafColumnAllTests[group])) > 1

        name = group[-1] if group else ""

        if not group:
            canCollapse = False

        #disable for now
        return name

        if canExpand:
            expansions = dict(self.columnExpansions)
            expansions[group] = {"type": "env", "prefix": len(group)}
            name = HtmlGeneration.link(name, url_fun(expansions)).render()

        if canCollapse:
            expansions = dict(self.columnExpansions)
            del expansions[group]
            name = HtmlGeneration.link(name, url_fun(expansions)).render()

        return name

    def render_row(self, row_identifier, url_fun):
        return [
            TestSummaryRenderer.TestSummaryRenderer(
                self, 
                self.tests_in_rows_and_columns[row_identifier].get(c, [])
                ).renderSummary()
                for c in self.columnsInOrder()
            ]
