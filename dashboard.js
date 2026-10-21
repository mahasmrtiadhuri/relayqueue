'use strict';
let token = '';
const $ = selector => document.querySelector(selector);
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function cell(text) { const el = document.createElement('td'); el.textContent = text; return el; }
async function refresh() {
  try {
    const [health, {jobs}] = await Promise.all([api('/health'), api('/api/jobs')]);
    $('#connection').textContent = '● Connected';
    const cards = Object.entries(health.counts).map(([name, count]) => { const card = document.createElement('article'); const value = document.createElement('strong'); value.textContent = count; const label = document.createElement('span'); label.textContent = name; card.append(value, label); return card; });
    $('#stats').replaceChildren(...cards);
    $('#empty').hidden = jobs.length > 0;
    const rows = jobs.map(job => {
      const row = document.createElement('tr');
      row.append(cell(`${job.id.slice(0, 8)} / ${job.kind}`), cell(job.state), cell(`${job.attempts} / ${job.max_attempts}`));
      const actions = cell(''); const inspect = document.createElement('button'); inspect.className = 'secondary'; inspect.textContent = 'Inspect';
      inspect.onclick = async () => { try { const events = await api(`/api/jobs/${job.id}/events`); $('#events').textContent = JSON.stringify({job, ...events}, null, 2); $('#detail').open = true; } catch (error) { $('#notice').textContent = error.message; } };
      actions.append(inspect);
      if (job.state === 'dead') { const retry = document.createElement('button'); retry.textContent = 'Retry'; retry.onclick = async () => { try { await api(`/api/jobs/${job.id}/retry`, {method: 'POST', body: '{}'}); await refresh(); } catch (error) { $('#notice').textContent = error.message; } }; actions.append(retry); }
      row.append(actions); return row;
    });
    $('#jobs').replaceChildren(...rows);
  } catch (error) { $('#connection').textContent = '● Disconnected'; $('#notice').textContent = error.message; }
}
$('#connect').onclick = () => { token = $('#token').value; $('#token').value = ''; refresh(); };
$('#refresh').onclick = refresh;
$('#submit').onsubmit = async event => { event.preventDefault(); try { const payload = JSON.parse($('#payload').value); const job = await api('/api/jobs', {method: 'POST', body: JSON.stringify({kind: $('#kind').value, payload, max_attempts: Number($('#attempts').value)}), headers: {'Idempotency-Key': crypto.randomUUID()}}); $('#notice').textContent = `Enqueued ${job.id.slice(0, 8)}`; await refresh(); } catch (error) { $('#notice').textContent = error.message; } };
refresh();
setInterval(refresh, 3000);
