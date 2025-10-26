async function loadDepartments(){
  const res = await fetch("/api/departments");
  const data = await res.json();
  const sel = document.getElementById("department");
  (data.departments||[]).forEach(d=>{
    const opt=document.createElement("option");
    opt.value=d.code; opt.textContent=d.name; sel.appendChild(opt);
  });
}
async function send(){
  const body = {
    department_code: document.getElementById('department').value,
    name: document.getElementById('name').value || null,
    phone: document.getElementById('phone').value,
    source: 'web',
    comment: document.getElementById('comment').value || null,
  };
  const r = await fetch('/api/leads', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  alert(r.ok ? 'Заявка отправлена' : 'Ошибка');
}
loadDepartments();
document.getElementById('send').addEventListener('click', send);
