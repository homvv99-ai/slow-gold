(function(){
if(window.__slowgold)return;window.__slowgold=1;
var B="https://homvv99-ai.github.io/slow-gold/site/";
var s=document.currentScript;
var host=document.createElement("div");
if(s&&s.parentNode)s.parentNode.insertBefore(host,s);else document.body.appendChild(host);
var st=document.createElement("style");
st.textContent=".sg-badge{direction:rtl;font-family:Tahoma,Segoe UI,sans-serif;background:#1f1913;border:1px solid #d4af37;border-radius:10px;padding:10px 14px;margin:10px 0;color:#e8dcc0;font-size:14px;line-height:1.7;max-width:420px}.sg-badge a{color:#d4af37;text-decoration:none;font-weight:bold}.sg-badge .g{color:#7ec97e}.sg-badge .y{color:#d8c060}";
document.head.appendChild(st);
host.innerHTML="⏳ جارٍ فتح الدفتر…";
Promise.all([fetch(B+"data/signal.json").then(function(r){return r.json();}),fetch(B+"data/stats.json").then(function(r){return r.json();})]).then(function(a){
var g=a[0],t=a[1];
var state=g.state=="LONG"?'<span class="g">🟢 داخل السوق</span>':'<span class="y">🟡 خارج السوق — درع مرفوع</span>';
host.innerHTML='🐢 <b>Slow Gold — الذهب الصبور</b><br>'+state+' · يوم '+g.days_in_state+' · '+g.price+'$<br>معامل الربح: <b>'+t.profit_factor+'</b> · أيام (لا): <b>'+t.no_pct+'%</b> منذ 2020<br><a href="'+B+'" target="_blank">📒 افتح الدفتر العلني الموقّع</a>';
}).catch(function(){host.innerHTML='🐢 Slow Gold — <a href="'+B+'" target="_blank">الدفتر العلني الموقّع</a>';});
})();
