"""
HtmlGeneration

Simple utilities for generating HTML for the TestLooperHttpServer.
"""

import logging
import re
import cgi

headers = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">

<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/4.0.0/css/bootstrap.min.css" 
      integrity="sha384-Gn5384xqQ1aoWXA+058RXPxPg6fy4IWvTNh0E263XmFcJlSAwiGgFAW/dAiS6JXm" 
    crossorigin="anonymous">

<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/octicons/4.4.0/font/octicons.min.css"/>
<link rel="stylesheet" href="/css/test-looper.css"/>
<link rel="stylesheet" href="/css/prism.css"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/gitgraph.js/1.11.4/gitgraph.css"/>

</head>
<body>
<script src="/js/prism.js"></script>
<script src="/js/gitgraph.js"></script>
"""

footers = """
<script src="https://code.jquery.com/jquery-3.2.1.slim.min.js" integrity="sha384-KJ3o2DKtIkvYIK3UENzmM7KCkRr/rE9/Qpg6aAZGJwFDMVNA/GpGFF93hXpG5KkN" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.12.9/umd/popper.min.js" integrity="sha384-ApNbgh9B+Y1QKtv3Rn7W3mgPxhU9K/ScQsAP7hUibX39j7fakFPskvXusvfa0b4Q" crossorigin="anonymous"></script>
<script src="https://maxcdn.bootstrapcdn.com/bootstrap/4.0.0/js/bootstrap.min.js" integrity="sha384-JZR6Spejh4U02d8jOt6vLEHfe/JQGiRRSQQxSfFWpi1MquVdAyjUar5+76PVCmYl" crossorigin="anonymous"></script>


<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/prism.js" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/components/prism-yaml.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/plugins/line-numbers/prism-line-numbers.js"></script>
<script> 
$(function () {
  $('[data-toggle="tooltip"]').tooltip()
})
</script>
</body>
<html>
"""

def gitgraph_canvas_setup(commit_generation, to_the_right):
    return """
<div style="width:3000px">
    <div style="display:inline-block; vertical-align: top">
        <div style="height: 40px"></div>
        <canvas id='gitGraph'></canvas>
    </div>
    <div style="width:1500px;display:inline-block">
    """ + to_the_right + """
    </div>
</div>

<script> 

var templateConfig = {
    branch: {
        color: "#000000",
        lineWidth: 3,
        spacingX: 50,
        mergeStyle: "straight",
        labelRotation: 0,
        mergeStyle: "bezier"
        },
    commit: {
        spacingY: 36,
        dot: {
            size: 5,
            strokeColor: "#000000",
            strokeWidth: 2
            },
        message: {
            display: false
            }
        },
    arrow: {
        active: false,
        size: 0,
        offset: 2.5
        }
    };

var template = new GitGraph.Template( templateConfig );

var gitgraph = new GitGraph({
  template: template,
  orientation: "vertical",
  author: ""
});

gitgraph.template.commit.message.font = "normal 12pt Calibri";

""" + commit_generation + """
</script>
"""

def render(x):
    if isinstance(x, HtmlElement):
        return x.render()
    return x

class HtmlElement(object):
    """Models an arbitrary string of html that will show up as fixed-width text."""
    def elementList(self):
        return [self]

    def __add__(self, other):
        if isinstance(other, basestring):
            return self + HtmlString(other)

        return HtmlElements(self.elementList() + other.elementList())

    def __radd__(self, other):
        if isinstance(other, basestring):
            return HtmlString(other) + self

        return HtmlElements(other.elementList() + self.elementList())

    def __len__(self):
        return 0

    def render(self):
        return ""

class TextTag(HtmlElement):
    def __init__(self, tag, contained, mods=None):
        self.tag = tag
        self.contained = makeHtmlElement(contained)
        self.mods = mods or {}

    def __len__(self):
        return len(self.contained)

    def render(self):
        return (("<%s " % self.tag) +
                " ".join(['%s="%s"' % (k, v) for k, v in self.mods.iteritems()]) + ">" +
                self.contained.render() + "</%s>" % self.tag)

class ParagraphTag(TextTag):
    def __init__(self, contained, mods):
        if isinstance(contained, TextTag):
            for k, v in contained.mods.iteritems():
                mod = mods.get(k)
                mods[k] = "%s %s" % (mod, v) if k else v
            contained = contained.contained

        super(ParagraphTag, self).__init__('p', contained, mods)

class PreformattedTag(TextTag):
    def __init__(self, contained):
        super(PreformattedTag, self).__init__('pre', contained)

class BoldTag(TextTag):
    def __init__(self, contained):
        super(BoldTag, self).__init__('strong', contained)


class SpanTag(HtmlElement):
    def __init__(self, contained, mods):
        self.contained = makeHtmlElement(contained)
        self.mods = mods

    def __len__(self):
        return len(self.contained)

    def render(self):
        return ("<span " + " ".join(['%s="%s"' % (k,v) for k,v in self.mods.iteritems()]) + ">" +
                self.contained.render() + "</span>")

def makeHtmlElement(elt):
    if isinstance(elt, HtmlElement):
        return elt
    return HtmlString(str(elt))

class HtmlString(HtmlElement):
    def __init__(self, text):
        self.text = text.encode('ascii', 'xmlcharrefreplace')

    def render(self):
        return self.text

    def __len__(self):
        special_symbols = re.findall(r"&\w+;", self.text)
        return len(self.text) + len(special_symbols) - sum(len(s) for s in special_symbols)

class HtmlElements(HtmlElement):
    """Models several concatenated html elements"""
    def __init__(self, elts):
        self.elts = elts
        self.lengthStash = None

    def elementList(self):
        return self.elts

    def render(self):
        return "".join([x.render() for x in self.elts])

    def __len__(self):
        if self.lengthStash is None:
            self.lengthStash = sum([len(x) for x in self.elts])
        return self.lengthStash

class Link(HtmlElement):
    def __init__(self, url, text, hover_text=None, is_button=False, button_style=None, new_tab=False):
        self.url = url
        self.text = text
        self.new_tab = new_tab
        self.hover_text = hover_text or ''
        self.is_button = is_button
        self.button_style = button_style or "btn-sm btn-primary"

    def __len__(self):
        return len(self.text)

    def render(self):
        button_class = ('class="btn %s" role="button"' % self.button_style) if self.is_button else ''
        return """<a href="%s" title="%s" %s %s>%s</a>""" % (
            self.url, cgi.escape(self.hover_text, quote=True), button_class, 'target="_blank"' if self.new_tab else "", render(self.text)
            )

    def withTextReplaced(self, newText):
        return Link(self.url, newText, self.hover_text)


whitespace = "&nbsp;"

def pad(s, length):
    text_length = len(s)
    if text_length < length:
        return s + whitespace  * (length - text_length)
    return s


def link(linkTxt, linkUrl, hover_text=None):
    return Link(linkUrl, linkTxt, hover_text)


def stack(*elements):
    return "".join(str(x) for x in elements)

def button(value, linkVal):
    return """
    <form action=\"%s\">
        <input type="submit" value=\"%s\"/>
    </form>
    """ % (linkVal, value)


def elementTextLength(e):
    e = e.render() if isinstance(e, HtmlElement) else str(e)
    text_length = sum(len(s[s.find('>')+1:]) for s in e.split('<'))
    logging.info("Text length: %d, Element: %s", text_length, e)
    return text_length

def grid(rows, header_rows=1, rowHeightOverride=None):
    """Given a list-of-lists (e.g. row of column values), format as a grid.

    We compute the width of each column (assuming null values if a column
    is not entirely populated).
    """
    if rowHeightOverride is not None:
        override_text = ' style="height:%spx"' % rowHeightOverride
    else:
        override_text = ""

    def row_colcount(row):
        cols = 0
        for c in row:
            if isinstance(c,dict) and 'colspan' in c:
                cols += c['colspan']
            else:
                cols += 1
        return cols

    col_count = row_colcount(rows[0])

    def format_cell(c, which='td'):
        if isinstance(c, dict):
            extras = ""
            if 'colspan' in c:
                extras += ' colspan="%d"' % c['colspan']

            return '<%s class="fit"%s>%s</%s>' % (which, extras, makeHtmlElement(c['content']).render(), which)
        else:
            return '<%s class="fit">%s</%s>' % (which, makeHtmlElement(c).render(), which)

    table_headers = "\n".join(
        "<tr%s>%s</tr>" % (override_text, "\n".join(format_cell(h, "th")
                                  for h in row))
        for row in rows[:header_rows])

    def format_row(row):
        if len(row) == 0:
            return '<tr class="blank_row"><td colspan="%d"/></tr>' % col_count
        else:
            cols = row_colcount(row)

            tr = "<tr" + override_text + ">%s" % "\n".join(format_cell(c) for c in row)

            if cols < col_count:
                tr += '<td colspan="%d"/>' % (col_count - cols)

            return tr + "</tr>"

    table_rows = "\n".join(format_row(row) for row in rows[header_rows:])

    format_str = ('<table class="table table-hscroll table-sm table-striped">'
                  '{headers}\n{rows}'
                  '</table>')
    return format_str.format(
        headers=table_headers,
        rows=table_rows
        )

def lightGrey(text):
    return ParagraphTag(text, {"class": "text-muted"})

def red(text):
    return ParagraphTag(text, {"class": "text-danger"})

def greenBacking(text):
    return ParagraphTag(text, {"class": "bg-success"})

def redBacking(text):
    return ParagraphTag(text, {"class": "bg-danger"})

def blueBacking(text):
    return ParagraphTag(text, {"class": "bg-info"})

def lightGreyBacking(text):
    return SpanTag(text, {'style': "background-color:#dddddd"})

def lightGreyWithHover(text, title):
    return SpanTag(text, {'class': "text-muted", 'title': cgi.escape(title, quote=True)})

def redWithHover(text, title):
    return SpanTag(text, {'class': "text-danger", 'title': cgi.escape(title, quote=True)})

def selectBox(name, items, default=None):
    '''
    items - a list of (value, caption) tuples representing the items in the select box.
    '''
    options = ['<option value="%s" %s>%s</option>' % (v, "selected" if v == default else '', t) \
               for v, t in items]

    return '<select class="form-control" name=%s>%s</select>' % (name, '\n'.join(options))
