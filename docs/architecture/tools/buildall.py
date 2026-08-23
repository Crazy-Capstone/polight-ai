# -*- coding: utf-8 -*-
"""두 다이어그램을 한 .drawio 파일(탭 2개)로 묶고, 검증용 미리보기를 뽑는다."""
from gen import write
import build1, build2

for d in (build1.d, build2.d):
    for n in d.nodes:
        # draw.io 는 'key=value' 토큰과 맨 앞의 스타일명만 허용한다.
        # 로고 data URI 에 ';' 나 '=' 가 남아 있으면 여기서 걸린다.
        BARE = {'text'}
        for tok in [t for t in n['style'].split(';') if t]:
            assert '=' in tok or tok in BARE, \
                'draw.io 스타일 토큰이 깨졌다: %r (node %s)' % (tok, n['id'])
    ids = {n['id'] for n in d.nodes}
    for e in d.edges:
        assert e['src'] in ids and e['dst'] in ids, '엣지 끝점 없음: %s' % e

write('polight-architecture.drawio', [build1.d, build2.d])
open('preview1.svg', 'w').write(build1.d.preview())
open('preview2.svg', 'w').write(build2.d.preview())
print('nodes:', len(build1.d.nodes), len(build2.d.nodes),
      '| edges:', len(build1.d.edges), len(build2.d.edges))
