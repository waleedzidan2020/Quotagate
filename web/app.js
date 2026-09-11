(()=>{
  function load(src,done){const s=document.createElement('script');s.src=src;s.onload=done||null;s.onerror=()=>console.error('Failed to load '+src);document.head.appendChild(s)}
  load('app-core.js',()=>load('speed-toggle.js',()=>load('guest-shaping.js',()=>load('guest-mode.js',()=>load('quota-ui.js',()=>load('live-usage.js',()=>load('maintenance.js',()=>load('qos-gaming.js',()=>load('static-ip.js')))))))));
})();
