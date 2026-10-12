'use strict';
const easy=document.querySelector('#easy'), english=document.querySelector('#english');
function preference(name,value){try{sessionStorage.setItem(name,value)}catch(e){}}
function setEasy(on){document.body.classList.toggle('easy',on);easy?.setAttribute('aria-pressed',String(on));const label=easy?.querySelector('[data-label]');if(label)label.textContent=on?'退出易用模式':'易用模式';window.addEnglishHints?.();}
easy?.addEventListener('click',()=>{const on=!document.body.classList.contains('easy');setEasy(on);preference('dental-easy',String(on));});
try{if(easy)setEasy(sessionStorage.getItem('dental-easy')==='true')}catch(e){}
function setEnglish(on){document.body.classList.toggle('show-english',on);english?.setAttribute('aria-pressed',String(on));const label=english?.querySelector('[data-label]');if(label)label.textContent=on?'关闭英文辅助':'英文辅助';window.addEnglishHints?.();window.setEnglishOptions?.(on);}
english?.addEventListener('click',()=>{const on=!document.body.classList.contains('show-english');setEnglish(on);preference('dental-english',String(on));});
try{setEnglish(sessionStorage.getItem('dental-english')==='true')}catch(e){setEnglish(false)}
const mobile=document.querySelector('#mobile-links');if(mobile){document.querySelectorAll('.sidebar nav a').forEach(a=>mobile.appendChild(a.cloneNode(true)));}
document.querySelectorAll('.sidebar nav a').forEach(a=>{if(a.pathname===location.pathname)a.setAttribute('aria-current','page');});
window.addEnglishHints?.();
document.querySelectorAll('[data-help]').forEach(b=>b.onclick=()=>document.querySelector('#help')?.showModal());
document.querySelectorAll('[data-print]').forEach(b=>b.onclick=()=>window.print());
document.querySelectorAll('form[data-guard]').forEach(f=>f.addEventListener('submit',()=>{f.querySelectorAll('button[type=submit],button:not([type])').forEach(b=>{b.dataset.originalLabel=b.innerHTML;b.disabled=true;b.textContent='正在保存…';});}));
window.addEventListener('pageshow',()=>{document.querySelectorAll('button[data-original-label]').forEach(b=>{b.disabled=false;b.innerHTML=b.dataset.originalLabel;delete b.dataset.originalLabel;});window.addEnglishHints?.();});
let utterance;
document.querySelector('[data-speak]')?.addEventListener('click',()=>{const status=document.querySelector('#speech-status');if(!('speechSynthesis' in window)){status.textContent='当前浏览器不支持语音，请阅读文字或联系前台。';return;}const voice=speechSynthesis.getVoices().find(v=>v.localService&&/^zh/i.test(v.lang));if(!voice){status.textContent='未检测到本机中文语音。请使用文字提示或请前台帮忙。';return;}speechSynthesis.cancel();utterance=new SpeechSynthesisUtterance('请核对预约时间和诊所地址。到诊后请先联系前台签到。如需帮助，请点击请前台帮我。');utterance.voice=voice;utterance.lang='zh-CN';utterance.onerror=()=>status.textContent='朗读不可用，请使用文字提示。';utterance.onend=()=>status.textContent='朗读结束。';speechSynthesis.speak(utterance);status.textContent='正在朗读通用操作提示。';});
document.querySelector('[data-stop]')?.addEventListener('click',()=>{if('speechSynthesis' in window)speechSynthesis.cancel();document.querySelector('#speech-status').textContent='已停止朗读。';});
window.addEventListener('pagehide',()=>{if('speechSynthesis' in window)speechSynthesis.cancel();});