(()=>{
// Compatibility shim only.
// Guest Mode UI and live status are owned by guest-shaping.js.
// The previous version of this file appended a second Guest panel and changed
// the new Mbps labels back to kbit after page load, which made values such as
// "2" become 2 kbit/s and could make clients appear to lose Internet.
function refreshGuest(){
  if(typeof window.loadGuestControlCenter==='function')window.loadGuestControlCenter();
}
const oldSave=window.saveNetwork;
if(typeof oldSave==='function')window.saveNetwork=async function(){
  const r=await oldSave.apply(this,arguments);
  setTimeout(refreshGuest,300);
  return r;
};
document.addEventListener('DOMContentLoaded',()=>setTimeout(refreshGuest,500));
})();
