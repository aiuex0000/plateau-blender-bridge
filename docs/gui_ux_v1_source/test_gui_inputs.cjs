const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(__dirname+'/PLATEAU_Bridge_操作画面プロトタイプ.html','utf8');
const src=html.split('/* PURE_START */')[1].split('/* PURE_END */')[0];
const ctx=vm.createContext({});vm.runInContext(src,ctx);
const tests=[
  ['中心点通常',()=>assert.equal(ctx.parseCoordinates('35.6896, 139.6917').lon,139.6917)],
  ['全角カンマ',()=>assert.equal(ctx.parseCoordinates('35.6，139.7').lat,35.6)],
  ['日本語読点',()=>assert.equal(ctx.parseCoordinates('35.6、139.7').lon,139.7)],
  ['余分な座標',()=>assert.throws(()=>ctx.parseCoordinates('35,139,400'))],
  ['HTML注入拒否',()=>assert.throws(()=>ctx.parseCoordinates('<script>,139'))],
  ['緯度範囲',()=>assert.throws(()=>ctx.parseCoordinates('91,135'))],
  ['経度範囲',()=>assert.throws(()=>ctx.parseCoordinates('35,181'))],
  ['URLは未対応',()=>assert.throws(()=>ctx.parseCoordinates('https://www.google.com/maps/@35,139,15z'))],
  ['矩形OK',()=>assert.equal(ctx.parseRectangle({west:'135',east:'136',south:'34',north:'35'}).north,35)],
  ['矩形逆転',()=>assert.throws(()=>ctx.parseRectangle({west:'136',east:'135',south:'34',north:'35'}))],
  ['矩形無効文字',()=>assert.throws(()=>ctx.parseRectangle({west:'abc',east:'136',south:'34',north:'35'}))],
  ['ファイル名OK',()=>assert.equal(ctx.normalizedName('shinjuku.blend'),'shinjuku.blend')],
  ['ファイル名拡張子',()=>assert.throws(()=>ctx.normalizedName('shinjuku.obj'))],
  ['不正パス記号',()=>assert.throws(()=>ctx.normalizedName('a/b.blend'))],
];
let fail=0;for(const [name,fn] of tests){try{fn();console.log('OK',name)}catch(e){console.error('FAIL',name,e);fail++}}
if(fail)process.exit(1);console.log(`${tests.length} input tests passed.`)
