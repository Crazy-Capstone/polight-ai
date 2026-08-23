# -*- coding: utf-8 -*-
"""Polight 아키텍처 다이어그램 생성기.
하나의 선언에서 draw.io(.drawio) 와 검증용 미리보기 SVG 를 함께 뽑는다."""
import base64, json, html
from urllib.parse import quote

ICONS = json.load(open('icons.json'))

def logo_svg(key):
    i = ICONS[key]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path fill="#%s" d="%s"/></svg>' % (i['hex'], i['path']))

def logo_uri(key, standard=False):
    """draw.io 는 스타일 문자열을 ';' 로, 키/값을 '=' 로 나눈다. base64 data URI 에는 ';base64,'
    가 들어가 스타일이 깨지고, drawio 가 쓰는 ';' 생략형은 브라우저가 못 읽는다(실측 확인).
    퍼센트 인코딩은 ';' 와 '=' 를 모두 %XX 로 바꾸므로 양쪽을 다 만족한다."""
    return 'data:image/svg+xml,' + quote(logo_svg(key), safe='')


PTS = ('points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],'
       '[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];')

# draw.io 내장 AWS4 스텐실. 이름과 색은 drawio 저장소의 Sidebar-AWS4.js 에서 확인한 값.
AWS_GROUP = {
    'cloud':  'grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;dashed=0;',
    'region': 'grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#147EBA;dashed=1;',
    'vpc':    'grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;fontColor=#8C4FFF;dashed=0;',
    'public': 'grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;dashed=0;',
    'ec2':    'grIcon=mxgraph.aws4.group_ec2_instance_contents;strokeColor=#D86613;fillColor=none;fontColor=#D86613;dashed=0;',
}
AWS_RES = {'s3': '#7AA116', 'ecr': '#ED7100', 'systems_manager': '#E7157B', 'ec2': '#ED7100'}


def st_group(kind):
    return (PTS + 'outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=13;'
            'fontStyle=1;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;'
            'shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;' + AWS_GROUP[kind])

def st_plain_group(stroke, fill, font):
    return ('rounded=1;arcSize=4;whiteSpace=wrap;html=1;dashed=1;dashPattern=6 4;strokeWidth=1;'
            'container=1;collapsible=0;pointerEvents=0;recursiveResize=0;verticalAlign=top;'
            'align=left;spacingLeft=12;spacingTop=2;fontSize=13;fontStyle=1;'
            'strokeColor=%s;fillColor=%s;fontColor=%s;' % (stroke, fill, font))

def st_aws_res(res):
    return ('sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=%s;'
            'strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;'
            'html=1;fontSize=11;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;'
            'resIcon=mxgraph.aws4.%s;' % (AWS_RES[res], res))

def st_aws_general(name):
    return ('sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=#232F3D;'
            'strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;'
            'html=1;fontSize=11;fontStyle=1;aspect=fixed;shape=mxgraph.aws4.%s;' % name)

def st_logo(key):
    return ('shape=image;imageAspect=0;aspect=fixed;html=1;verticalLabelPosition=bottom;'
            'verticalAlign=top;labelBackgroundColor=none;align=center;fontSize=11;'
            'image=%s;' % logo_uri(key))

def st_box(stroke, fill, font, bold=0):
    return ('rounded=1;arcSize=12;whiteSpace=wrap;html=1;strokeColor=%s;fillColor=%s;fontColor=%s;'
            'fontSize=11;fontStyle=%d;verticalAlign=middle;align=center;' % (stroke, fill, font, bold))

EDGE = ('edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=auto;orthogonalLoop=1;'
        'strokeColor=#4A5568;strokeWidth=1.4;fontSize=10;fontColor=#2D3748;labelBackgroundColor=#FFFFFF;'
        'endArrow=blockThin;endFill=1;')
EDGE_DASH = EDGE + 'dashed=1;dashPattern=6 4;'


class Diagram:
    def __init__(self, name, w, h):
        self.name, self.w, self.h = name, w, h
        self.nodes, self.edges = [], []

    def node(self, nid, style, x, y, w, h, label='', parent=None):
        self.nodes.append(dict(id=nid, style=style, x=x, y=y, w=w, h=h, label=label, parent=parent))
        return nid

    def edge(self, src, dst, label='', style=EDGE, exit=None, entry=None, lpos=None):
        s = style
        if exit:
            s += 'exitX=%s;exitY=%s;exitDx=0;exitDy=0;' % exit
        if entry:
            s += 'entryX=%s;entryY=%s;entryDx=0;entryDy=0;' % entry
        self.edges.append(dict(src=src, dst=dst, label=label, style=s, lpos=lpos))

    def _abs(self, nid):
        n = {x['id']: x for x in self.nodes}[nid]
        x, y = n['x'], n['y']
        while n['parent']:
            n = {x['id']: x for x in self.nodes}[n['parent']]
            x += 0; y += 0  # 좌표는 이미 절대값으로 선언한다
        return x, y

    def drawio(self):
        by_id = {n['id']: n for n in self.nodes}
        out = []
        for n in self.nodes:
            px, py = 0, 0
            if n['parent']:
                p = by_id[n['parent']]
                px, py = p['x'], p['y']
            out.append(
                '        <mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">\n'
                '          <mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/>\n'
                '        </mxCell>' % (n['id'], html.escape(n['label'], quote=True), n['style'],
                                       n['parent'] or '1', n['x'] - px, n['y'] - py, n['w'], n['h']))
        for i, e in enumerate(self.edges):
            out.append(
                '        <mxCell id="e%d" value="%s" style="%s" edge="1" parent="1" source="%s" target="%s">\n'
                '          <mxGeometry%s relative="1" as="geometry"><mxPoint as="offset"/></mxGeometry>\n'
                '        </mxCell>' % (i, html.escape(e['label'], quote=True), e['style'], e['src'], e['dst'],
                               '' if e.get('lpos') is None else ' x="%s"' % e['lpos']))
        return ('    <diagram id="%s" name="%s">\n'
                '      <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1"'
                ' connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d"'
                ' math="0" shadow="0">\n        <root>\n'
                '        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n%s\n'
                '        </root>\n      </mxGraphModel>\n    </diagram>'
                % (self.name.replace(' ', '-'), html.escape(self.name, quote=True),
                   self.w, self.h, '\n'.join(out)))

    # ---- 검증용 미리보기. 좌표/라벨이 겹치지 않는지 눈으로 확인하기 위한 것이다 ----
    def preview(self):
        p = ['<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
             'width="%d" height="%d" viewBox="0 0 %d %d" font-family="sans-serif">' % (self.w, self.h, self.w, self.h),
             '<rect width="100%" height="100%" fill="#fff"/>']
        def sval(style, key, dflt=''):
            for kv in style.split(';'):
                if kv.startswith(key + '='):
                    return kv[len(key) + 1:]
            return dflt
        groups = [n for n in self.nodes if 'container=1' in n['style']]
        others = [n for n in self.nodes if 'container=1' not in n['style']]
        for n in groups:
            stroke = sval(n['style'], 'strokeColor', '#888')
            fill = sval(n['style'], 'fillColor', 'none')
            if fill == 'none':
                fill = 'none'
            p.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="%s" '
                     'stroke-dasharray="6 4" rx="4"/>' % (n['x'], n['y'], n['w'], n['h'], fill, stroke))
            p.append('<text x="%d" y="%d" font-size="13" font-weight="bold" fill="%s">%s</text>'
                     % (n['x'] + 26, n['y'] + 17, sval(n['style'], 'fontColor', '#333'),
                        html.escape(n['label'])))
        pos = {n['id']: (n['x'] + n['w'] / 2, n['y'] + n['h'] / 2) for n in self.nodes}
        for e in self.edges:
            x1, y1 = pos[e['src']]; x2, y2 = pos[e['dst']]
            dash = ' stroke-dasharray="5 4"' if 'dashed=1' in e['style'] else ''
            p.append('<line x1="%.0f" y1="%.0f" x2="%.0f" y2="%.0f" stroke="#4A5568" '
                     'stroke-width="1.2"%s/>' % (x1, y1, x2, y2, dash))
            if e['label']:
                lbl = e['label'].split('\n')[0][:34]
                p.append('<text x="%.0f" y="%.0f" font-size="9" fill="#1A365D" text-anchor="middle">'
                         '%s</text>' % ((x1 + x2) / 2, (y1 + y2) / 2, html.escape(lbl)))
        for n in others:
            img = sval(n['style'], 'image')
            if img:
                p.append('<image x="%d" y="%d" width="%d" height="%d" xlink:href="%s"/>'
                         % (n['x'], n['y'], n['w'], n['h'], html.escape(img, quote=True)))
            elif 'resourceIcon' in n['style'] or 'mxgraph.aws4' in n['style']:
                p.append('<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="%s"/>'
                         % (n['x'], n['y'], n['w'], n['h'], sval(n['style'], 'fillColor', '#ED7100')))
                tag = sval(n['style'], 'resIcon', 'aws').split('.')[-1][:4].upper()
                p.append('<text x="%d" y="%d" font-size="10" fill="#fff" text-anchor="middle" '
                         'font-weight="bold">%s</text>' % (n['x'] + n['w'] / 2, n['y'] + n['h'] / 2 + 4, tag))
            else:
                p.append('<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="%s" stroke="%s"/>'
                         % (n['x'], n['y'], n['w'], n['h'], sval(n['style'], 'fillColor', '#fff'),
                            sval(n['style'], 'strokeColor', '#999')))
            # 라벨
            below = 'verticalLabelPosition=bottom' in n['style']
            lines = n['label'].replace('<b>', '').replace('</b>', '').split('<br>')
            ty = n['y'] + n['h'] + 12 if below else n['y'] + n['h'] / 2 - (len(lines) - 1) * 6 + 4
            for i, ln in enumerate(lines):
                p.append('<text x="%d" y="%d" font-size="10" fill="#1A202C" text-anchor="middle">%s</text>'
                         % (n['x'] + n['w'] / 2, ty + i * 12, html.escape(ln)))
        p.append('</svg>')
        return '\n'.join(p)


def write(path, diagrams):
    body = '\n'.join(d.drawio() for d in diagrams)
    open(path, 'w').write('<mxfile host="app.diagrams.net" agent="polight" type="device">\n'
                          + body + '\n</mxfile>\n')
