const tipEl=document.getElementById("tip");
document.addEventListener("mousemove",e=>{
  const t=e.target.closest&&e.target.closest("[data-tip]");
  if(!t){tipEl.style.display="none";return;}
  tipEl.textContent=t.dataset.tip;
  tipEl.className=t.dataset.tip.length>80?"long":"";
  tipEl.style.display="block";
  const w=tipEl.offsetWidth,h=tipEl.offsetHeight;
  let x=e.clientX+12,y=e.clientY-h-10;
  if(x+w>innerWidth-8)x=Math.max(8,e.clientX-w-12);
  if(y<8)y=e.clientY+14;
  tipEl.style.left=x+"px";tipEl.style.top=y+"px";
});