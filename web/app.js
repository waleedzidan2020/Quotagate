(()=>{
  function load(src,done){const s=document.createElement('script');s.src=src;s.onload=done||null;s.onerror=()=>console.error('Failed to load '+src);document.head.appendChild(s)}
  load('app-core.js',()=>load('speed-toggle.js',()=>load('guest-shaping.js',()=>load('guest-mode.js'))));
})();
