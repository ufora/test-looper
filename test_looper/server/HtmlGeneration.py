"""
HtmlGeneration

Simple utilities for generating HTML for the TestLooperHttpServer.
"""

import uuid
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

<link href='//fonts.googleapis.com/css?family=Source+Sans+Pro:300,400,600,400italic' rel='stylesheet' type='text/css'>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/octicons/4.4.0/font/octicons.min.css"/>
<link rel="stylesheet" href="/css/test-looper.css"/>
<link rel="stylesheet" href="/css/datatables.min.css"/>
<link rel="stylesheet" href="/css/prism.css"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/gitgraph.js/1.11.4/gitgraph.css"/>

</head>
<body>
<script src="/js/prism.js"></script>
<script src="/js/gitgraph.js"></script>
"""

footers = """
<script
  src="https://code.jquery.com/jquery-3.3.1.min.js"
  integrity="sha256-FgpCb/KJQlLNfOu91ta32o/NMZxltwRo8QtmkMRdAu8="
  crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.12.9/umd/popper.min.js" integrity="sha384-ApNbgh9B+Y1QKtv3Rn7W3mgPxhU9K/ScQsAP7hUibX39j7fakFPskvXusvfa0b4Q" crossorigin="anonymous"></script>
<script src="https://maxcdn.bootstrapcdn.com/bootstrap/4.0.0/js/bootstrap.min.js" integrity="sha384-JZR6Spejh4U02d8jOt6vLEHfe/JQGiRRSQQxSfFWpi1MquVdAyjUar5+76PVCmYl" crossorigin="anonymous"></script>

<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/prism.js" crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/components/prism-yaml.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.11.0/plugins/line-numbers/prism-line-numbers.js"></script>

<script src="/js/datatables.min.js"></script>

<script> 
$(function () {
  $('[data-toggle="tooltip"]').tooltip({
    template: '<div class="tooltip" role="tooltip"><div class="tooltip-arrow"></div><div class="tooltip-inner large"></div></div>'
    });
})
const getChildProp = function(el, child) {
  return $('.data-' + child, $(el).attr('data-bind')).html();
};

$('.popover-dismiss').popover({
  trigger: 'focus'
});

$('[data-table-enabled="true"]').DataTable({
    paging: false
    });

$('[data-toggle="popover"]').popover({
  html: true,
  container: 'body',
  placement: 'bottom',
  title: function () {
    return getChildProp(this, 'title');
  },
  content: function () {
    return getChildProp(this, 'content');
  },
  placement: function () {
    return getChildProp(this, 'placement');
  }
});
$('[data-poload]').on('show.bs.dropdown', function (arg) {
  var target = arg.currentTarget;

  var ref=$(target).attr("data-poload");
  var tgt=$($(target).attr("data-poload-target"))[0];

  $(tgt).load(ref)
})
</script>
</body>
<html>
"""

def gitgraph_canvas_setup(commit_generation, to_the_right):
    return """
<div style="width:3000px">
    <div style="display:inline-block; vertical-align: top">
        <div style="height: 4px"></div>
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
        if isinstance(other, str):
            return self + HtmlString(other)

        return HtmlElements(self.elementList() + other.elementList())

    def __radd__(self, other):
        if isinstance(other, str):
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
                " ".join(['%s="%s"' % (k, v) for k, v in self.mods.items()]) + ">" +
                self.contained.render() + "</%s>" % self.tag)

class ParagraphTag(TextTag):
    def __init__(self, contained, mods):
        if isinstance(contained, TextTag):
            for k, v in contained.mods.items():
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
        return ("<span " + " ".join(['%s="%s"' % (k,v) for k,v in self.mods.items()]) + ">" +
                self.contained.render() + "</span>")

def makeHtmlElement(elt):
    if isinstance(elt, HtmlElement):
        return elt
    return HtmlString(str(elt))

class HtmlString(HtmlElement):
    def __init__(self, text):
        try:
            self.text = text.encode('ascii', 'xmlcharrefreplace').decode("ascii")
        except UnicodeDecodeError:
            self.text = "_bad unicode_"

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
        res = []
        for e in self.elts:
            r = e.render()
            assert isinstance(r, str), type(e)
            res.append(r)

        return "".join(res)

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

    def withTextReplaced(self, newText, hoverText=None):
        return Link(self.url, newText, hoverText if hoverText is not None else self.hover_text)


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

def popover(contents, detail_title, detail_view, width, data_placement=None):
    divid = str(uuid.uuid4())

    return """
        <a href="#" data-toggle="popover" data-trigger="focus" data-bind="#{div}" container="body" class="btn btn-xs" role="button">{button_text}</a>
        <div style="display:none;">
          <div id="{div}">
            <div class='data-placement'>{placement}</div>
            <div class="data-title">{detail_title}</div>
            <div class="data-content"><div style="width:{width}px">{detail_view}</div></div>
          </div>
        </div>
        """.format(div=divid, button_text=contents, detail_title=detail_title, detail_view=detail_view, width=width, 
            placement=data_placement or "bottom")



def elementTextLength(e):
    e = e.render() if isinstance(e, HtmlElement) else str(e)
    text_length = sum(len(s[s.find('>')+1:]) for s in e.split('<'))
    logging.info("Text length: %d, Element: %s", text_length, e)
    return text_length

def transposeGrid(grid):
    colcount = max([len(x) for x in grid])
    rowcount = len(grid)
    return [[grid[y][x] if x < len(grid[y]) else "" for y in range(rowcount)] for x in range(colcount)]

def grid(rows, header_rows=1, rowHeightOverride=None, fitWidth=True, transpose=False, dataTables=False):
    """Given a list-of-lists (e.g. row of column values), format as a grid.

    We compute the width of each column (assuming null values if a column
    is not entirely populated).
    """
    if transpose:
        rows=transposeGrid(rows)

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

    def format_cell(c, which='td',extra_classes="pr-5"):
        if isinstance(c, dict):
            extras = ""
            if 'colspan' in c:
                extras += ' colspan="%d"' % c['colspan']

            class_elt = c.get('class','')

            return '<%s class="%s %s %s"%s>%s</%s>' % (which, "fit" if fitWidth else "", extra_classes, class_elt, extras, makeHtmlElement(c['content']).render(), which)
        else:
            return '<%s class="%s %s">%s</%s>' % (which, "fit" if fitWidth else "", extra_classes, makeHtmlElement(c).render(), which)

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

    if dataTables:
        format_str = ('<table class="table-hscroll table-sm table-striped" data-table-enabled="true">'
                      '<thead>{headers}</thead>\n<tbody>{rows}</tbody>'
                      '</table>')
    else:
        format_str = ('<table class="table-hscroll table-sm table-striped">'
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

def secondsUpToString(up_for):
    if up_for < 60:
        return ("%d seconds" % up_for)
    elif up_for < 60 * 60 * 2:
        return ("%.1f minutes" % (up_for / 60))
    elif up_for < 24 * 60 * 60 * 2:
        return ("%.1f hours" % (up_for / 60 / 60))
    else:
        return ("%.1f days" % (up_for / 60 / 60 / 24))



def octicon(text, extra=""):
    return '<span class="octicon octicon-%s %s" aria-hidden="true"></span>' % (text,extra)

def bytesToHumanSize(bytes):
    if bytes is None:
        return ""

    if bytes < 1024 * 2:
        return "%s bytes" % bytes

    if bytes < 1024 * 2 * 1024:
        return "%.1f Kb" % (bytes / 1024.0)

    if bytes < 1024 * 2 * 1024 * 1024:
        return "%.1f Mb" % (bytes / 1024.0 / 1024.0)

    return "%.1f Gb" % (bytes / 1024.0 / 1024.0 / 1024.0)

def card(text):
    return """<div class="card">
                  <div class="card-body">
                    {text}
                  </div>
                </div>""".format(text=text)

def tabs(name, tabSeq):
    pils = []
    bodies = []

    for ix in range(len(tabSeq)):
        header, contents, selector = tabSeq[ix]

        active = "active" if ix == 0 else ""
        pils.append(
            """
            <li class="nav-item">
                <a class="nav-link {active}" id="{selector}-tab" data-toggle="tab" href="#{selector}" role="tab" aria-controls="{selector}" aria-selected="{selected}">
                    {header}
                </a>
              </li>
            """.format(active=active, selector=selector, header=header, selected=ix==0)
            )

        bodies.append(
            """
            <div class="tab-pane fade {show} {active}" id="{selector}" role="tabpanel" aria-labelledby="{selector}-tab">{contents}</div>
            """.format(selector=selector,contents=contents, active=active, show="show" if ix == 0 else "")
            )

    return ("""<div class="container-fluid mb-3">
                     <ul class="nav nav-pills" id="{name}" role="tablist">
                      {pils}
                    </ul>
                    <div class="tab-content" id="{name}Content">
                      {body}
                    </div>
                </div>
                """.format(pils="".join(pils), body="".join(bodies),name=name))

def urlDropdown(contents, url):
    return '''
        <div class="btn-group" data-poload="{url}" data-poload-target="#{guid}">
        <button class="btn btn-xs btn-outline-secondary dropdown-toggle" type="button" data-toggle="dropdown">
            {contents}
        </button>
        <div class="dropdown-menu" aria-labelledby="dropdownMenuButton" id="{guid}">
            <div style="width:30px;margin-top:20px;margin-bottom:20px;margin:auto"><div class="loader"></div></div>
        </div>
        </div>
        '''.format(
            guid=str(uuid.uuid4()).replace("-",""),
            contents=contents,
            url=url
            )

class Redirect:
    def __init__(self, url):
        self.url = url