const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../assets/app_fixed.js'), 'utf8');
const functions = source.slice(source.indexOf('  function saveLiveView()'), source.indexOf("  window.addEventListener('pageshow'"));
function setup(value) {
  const ctx = vm.createContext({localStorage: {getItem: () => value, setItem: (_, v) => {value = v;}}, location: {pathname:'/project/live.php'}});
  vm.runInContext(`let livePage=1, livePageSize=2, recorderFilter='', cameraHealthFilter='';
    const viewKey='project'; const selectedCameraKeys=new Set();
    const cameras=[{camera_key:'a',ip:'recorder'}, {camera_key:'b',ip:'recorder'}]; ${functions}
    function result() {return JSON.stringify({page:livePage,size:livePageSize,recorder:recorderFilter,health:cameraHealthFilter,selected:[...selectedCameraKeys]});}`, ctx);
  return {run: code => vm.runInContext(code, ctx), get: () => JSON.parse(vm.runInContext('result()', ctx))};
}
test('returning to live restores page 5, six cameras, filters and selection', () => {
 const s=setup(null);
 s.run("livePage=5;livePageSize=6;recorderFilter='recorder';selectedCameraKeys.add('b');saveLiveView();livePage=1;livePageSize=2;selectedCameraKeys.clear();restoreLiveView();");
 assert.deepEqual(s.get(),{page:5,size:6,recorder:'recorder',health:'',selected:['b']});
});
test('deleted cameras and invalid saved preferences are discarded', () => {
 const s=setup(JSON.stringify({page:-2,size:999,recorder:'gone',health:'invalid',selected:['gone','a']}));s.run('restoreLiveView()');
 assert.deepEqual(s.get(),{page:1,size:2,recorder:'',health:'',selected:['a']});
});
test('corrupt storage does not prevent opening live page', () => {
 const s=setup('bad JSON');s.run('restoreLiveView()');assert.equal(s.get().page,1);
});
