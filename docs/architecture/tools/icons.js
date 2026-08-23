const si = require('simple-icons');
const map = {flutter:'siFlutter', vercel:'siVercel', spring:'siSpringboot', docker:'siDocker',
  fastapi:'siFastapi', python:'siPython', postgresql:'siPostgresql', github:'siGithub',
  gemini:'siGooglegemini', claude:'siClaude', gmaps:'siGooglemaps', kakao:'siKakaotalk'};
const out = {};
for (const [k, v] of Object.entries(map)) {
  const i = si[v];
  if (!i) throw new Error('missing ' + v);
  out[k] = {path: i.path, hex: i.hex, title: i.title};
}
require('fs').writeFileSync('icons.json', JSON.stringify(out, null, 1));
console.log('ok', Object.keys(out).join(' '));
