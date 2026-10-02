'use strict';
const form=document.querySelector('#login-form'),message=document.querySelector('#login-error'),submit=document.querySelector('#submit');
const password=document.querySelector('#password'),show=document.querySelector('#show-password'),remember=document.querySelector('#remember');
show.addEventListener('click',()=>{const visible=password.type==='password';password.type=visible?'text':'password';show.textContent=visible?'숨김':'표시';show.setAttribute('aria-pressed',String(visible));show.setAttribute('aria-label',visible?'비밀번호 숨기기':'비밀번호 표시');});
form.addEventListener('submit',async event=>{
  event.preventDefault();message.hidden=true;submit.disabled=true;submit.textContent='확인 중…';
  try{
    const response=await fetch('/auth/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.querySelector('#username').value.trim(),password:password.value,remember:remember.checked})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.detail||'로그인하지 못했습니다.');
    password.value='';
    const add=new URLSearchParams(location.search).get('add');
    location.replace(add&&add.length<=2048?'/?add='+encodeURIComponent(add):'/');
  }catch(e){message.textContent=e instanceof TypeError?'서버에 연결하지 못했습니다. PC와 연결 앱 상태를 확인하세요.':e.message;message.hidden=false;submit.disabled=false;submit.textContent='로그인';}
});
