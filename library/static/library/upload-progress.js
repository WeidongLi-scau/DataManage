document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('.material-fields-form');
  const panel = document.querySelector('#upload-progress-panel');
  if (!form || !panel) return;
  const totalProgress = panel.querySelector('progress');
  const status = panel.querySelector('[data-upload-status]');
  const counts = panel.querySelector('[data-upload-count]');
  const bytes = panel.querySelector('[data-upload-bytes]');
  const error = panel.querySelector('[data-upload-error]');
  const result = panel.querySelector('[data-upload-result]');
  const edit = panel.querySelector('[data-upload-edit]');
  const newUpload = panel.querySelector('[data-upload-new]');
  const saved = new Set();
  let busy = false, batchId = null;
  const size = value => value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`;
  const leaving = event => { event.preventDefault(); event.returnValue = ''; };
  edit.addEventListener('click', () => {
    form.inert = false;
    form.hidden = false;
    panel.hidden = true;
    form.querySelector('button[type="submit"]').focus();
  });
  form.addEventListener('files-ready-to-upload', async event => {
    event.preventDefault();
    if (busy) return;
    const entries = event.detail.entries.filter(entry => !saved.has(entry));
    const total = entries.reduce((sum, entry) => sum + entry.file.size, 0);
    panel.hidden = false; error.hidden = edit.hidden = result.hidden = newUpload.hidden = true;
    if (total > 1024 ** 3) {
      error.textContent = '本次上传总量超过 1 GB，请移除部分文件后重试。';
      error.hidden = edit.hidden = false;
      panel.focus(); return;
    }
    const metadata = new FormData(form);
    metadata.delete('files'); metadata.delete('relative_paths');
    Array.from(metadata.keys()).filter(key => /^file_\d+_/.test(key)).forEach(key => metadata.delete(key));
    const list = panel.querySelector('[data-upload-files]'); list.replaceChildren();
    const rows = entries.map(entry => {
      const row = document.createElement('li');
      const name = document.createElement('span'); name.className = 'upload-progress-name'; name.textContent = entry.path;
      const label = document.createElement('span'); label.className = 'upload-file-state'; label.textContent = '等待上传';
      const progress = document.createElement('progress'); progress.max = 100; progress.value = 0;
      progress.setAttribute('aria-label', `${entry.path} 传输进度`);
      row.append(name, label, progress); list.append(row); return { row, label, progress };
    });
    form.inert = true; form.hidden = true; panel.focus();
    window.addEventListener('beforeunload', leaving);
    busy = true; let completed = 0, transferred = 0, stopped = false;
    const update = loaded => {
      totalProgress.value = total ? Math.min(100, (transferred + loaded) / total * 100) : completed / Math.max(1, entries.length) * 100;
      bytes.textContent = `已传输 ${size(transferred + loaded)} / ${size(total)} · ${Math.floor(totalProgress.value)}%（传输完成后仍需保存与处理）`;
      counts.textContent = `已保存 ${completed} / ${entries.length} 个文件`;
    };
    update(0);
    try {
      for (let index = 0; index < entries.length; index++) {
        const entry = entries[index], view = rows[index];
        const data = new FormData(); metadata.forEach((value, key) => data.append(key, value));
        data.set('response_mode', 'progress');
        if (batchId) data.set('progress_batch', batchId);
        data.set('files', entry.file, entry.file.name);
        data.set('relative_paths', JSON.stringify([entry.path || entry.file.name]));
        entry.row.querySelectorAll('[data-file-field]').forEach(control => data.set(`file_0_${control.dataset.fileField}`, control.value));
        status.textContent = `正在上传第 ${index + 1} / ${entries.length} 个：${entry.path}`;
        view.row.dataset.state = 'active'; view.label.textContent = '正在传输 · 0%';
        list.scrollTop = view.row.offsetTop;
        const response = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open('POST', form.action); xhr.responseType = 'json';
          xhr.upload.addEventListener('progress', progress => {
            if (!progress.lengthComputable) { view.progress.removeAttribute('value'); return; }
            const ratio = progress.total ? progress.loaded / progress.total : 1;
            view.progress.value = ratio * 100; update(entry.file.size * ratio);
            view.label.textContent = `正在传输 · ${Math.floor(ratio * 100)}%`;
          });
          xhr.upload.addEventListener('load', () => {
            view.progress.value = 100; update(entry.file.size);
            view.label.textContent = '传输完成，正在保存与处理';
            status.textContent = `第 ${index + 1} / ${entries.length} 个文件传输完成，等待服务器保存与处理：${entry.path}`;
          });
          xhr.addEventListener('load', () => resolve({ code: xhr.status, data: xhr.response }));
          xhr.addEventListener('error', () => reject(new Error('连接中断，当前文件是否保存尚不确定。请先查看“我的上传”或本批结果，避免重复上传。')));
          xhr.addEventListener('abort', () => reject(new Error('传输已中断，请先核对本批结果。')));
          xhr.send(data);
        });
        const dataResult = response.data;
        if (!dataResult || response.code !== 200) {
          const details = dataResult?.errors ? Object.values(dataResult.errors).flat().join('；') : '';
          const message = details || dataResult?.error || (response.code === 403 ? '登录状态或请求校验已失效，请重新登录后核对上传记录。' : '服务器响应异常，请先核对上传记录，避免重复提交。');
          view.label.textContent = '未完成'; view.row.dataset.state = 'failed';
          if (response.code === 422) edit.hidden = false;
          throw new Error(message);
        }
        batchId = dataResult.batch_id; result.href = dataResult.batch_url; result.hidden = false;
        view.progress.value = 100;
        transferred += entry.file.size;
        if (dataResult.saved) {
          saved.add(entry); completed++;
          view.label.textContent = dataResult.status === 'failed' ? '已保存 · 处理失败，可在结果页重试' : `已保存 · ${dataResult.status_label}`;
          view.row.dataset.state = dataResult.status === 'failed' ? 'warning' : 'done';
          if (dataResult.message) { const note = document.createElement('small'); note.textContent = dataResult.message; view.row.append(note); }
        } else {
          view.label.textContent = '保存失败'; view.row.dataset.state = 'failed'; edit.hidden = false;
          throw new Error(dataResult.message || '文件未能保存，请检查后重试。');
        }
        update(0);
      }
      status.textContent = `上传完成，${completed} 个文件已保存。请查看本批结果，确认预览与解析状态。`;
    } catch (failure) {
      stopped = true; status.textContent = '上传已暂停，后续文件尚未上传。';
      error.textContent = failure.message; error.hidden = false;
      const active = rows.find(view => view.row.dataset.state === 'active');
      if (active) { active.label.textContent = '结果待核对'; active.row.dataset.state = 'warning'; }
    } finally {
      busy = false; window.removeEventListener('beforeunload', leaving); newUpload.hidden = false;
      if (!stopped) form.inert = false;
    }
  });
});
