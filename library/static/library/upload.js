document.addEventListener("DOMContentLoaded", () => {
  const fileInput = document.querySelector("#id_files");
  const fileSummary = document.querySelector("#selected-files");
  const fileMetadata = document.querySelector("#per-file-metadata");
  const typeSelect = document.querySelector("#id_record_type");
  const publicationFields = document.querySelector("#publication-fields");
  const form = document.querySelector(".material-fields-form");
  const parseButton = document.querySelector("[data-parse-bibtex]");
  const parseStatus = document.querySelector("[data-bibtex-status]");
  const journalOptions = JSON.parse(document.querySelector("#journal-catalog-data")?.textContent || "[]");
  const journalNameInput = document.querySelector("[data-journal-name]");
  const journalIdInput = document.querySelector("#id_journal");
  const dateSummary = document.querySelector("[data-publication-date-summary]");
  const exactDateInput = document.querySelector("#id_publication_date");
  const yearInput = document.querySelector("#id_publication_year");
  const monthInput = document.querySelector("#id_publication_month");

  const updateDateSummary = () => {
    if (!dateSummary) return;
    if (exactDateInput?.value) {
      dateSummary.textContent = `已识别完整日期：${exactDateInput.value}`;
      return;
    }
    const year = yearInput?.value;
    const month = monthInput?.value;
    dateSummary.textContent = year
      ? `已识别：${year} 年${month ? ` ${month.padStart(2, "0")} 月` : ""}（原文未提供具体日期）`
      : "";
  };
  [exactDateInput, yearInput, monthInput].forEach((input) => input?.addEventListener("input", updateDateSummary));
  [yearInput, monthInput].forEach((input) => input?.addEventListener("change", updateDateSummary));
  updateDateSummary();

  const matchJournal = (name) => {
    const normalized = name.trim().replace(/\s+/g, " ").toLocaleLowerCase();
    const match = journalOptions.find((journal) => {
      const candidates = [journal.name, journal.abbreviation].filter(Boolean);
      return candidates.some((candidate) => candidate.trim().replace(/\s+/g, " ").toLocaleLowerCase() === normalized);
    });
    if (journalIdInput) journalIdInput.value = match?.id || "";
    if (match && journalNameInput) journalNameInput.value = match.name;
  };

  journalNameInput?.addEventListener("input", () => {
    if (journalIdInput) journalIdInput.value = "";
    const typedValue = journalNameInput.value.trim();
    const match = journalOptions.find((journal) => [journal.name, journal.abbreviation].filter(Boolean)
      .some((candidate) => candidate.trim().replace(/\s+/g, " ").toLocaleLowerCase() === typedValue.replace(/\s+/g, " ").toLocaleLowerCase()));
    if (match && journalIdInput) journalIdInput.value = match.id;
  });
  journalNameInput?.addEventListener("change", () => matchJournal(journalNameInput.value));
  if (journalNameInput?.value) matchJournal(journalNameInput.value);

  const queue = [];
  const commonTitle = document.querySelector("#id_title");
  const titleField = commonTitle?.closest("[data-field='title']");
  const dropZone = document.querySelector("#upload-drop-zone");
  const selectionError = document.querySelector("#file-selection-error");
  const clearFiles = document.querySelector("#clear-files");
  const formatSize = (size) => size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
  const fitTitle = (input) => {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight + 2}px`;
  };

  // Keep the picker separate from the submitted FileList, so later selections append
  // and the same file can be selected again without losing previous metadata.
  let submittedFiles;
  if (fileInput && typeof DataTransfer !== "undefined") {
    submittedFiles = document.createElement("input");
    submittedFiles.type = "file";
    submittedFiles.name = "files";
    submittedFiles.multiple = true;
    submittedFiles.hidden = true;
    fileInput.after(submittedFiles);
    fileInput.removeAttribute("name");
    fileInput.required = false;
    fileInput.classList.add("file-picker-enhanced");
    document.querySelector("[data-file-picker]").hidden = false;
    document.querySelector("[data-drop-hint]").hidden = false;
  }

  const syncQueue = () => {
    if (submittedFiles) {
      const transfer = new DataTransfer();
      queue.forEach((entry) => transfer.items.add(entry.file));
      submittedFiles.files = transfer.files;
      fileInput.setCustomValidity(queue.length ? "" : "请选择至少一个文件。");
    }
    queue.forEach((entry, index) => {
      entry.row.querySelectorAll("[data-file-field]").forEach((control) => {
        control.name = `file_${index}_${control.dataset.fileField}`;
      });
    });
    fileSummary.textContent = queue.length
      ? `已选择 ${queue.length} 个文件 · 共 ${formatSize(queue.reduce((sum, entry) => sum + entry.file.size, 0))}`
      : "尚未选择文件";
    clearFiles.hidden = !queue.length || !submittedFiles;
    if (titleField) titleField.hidden = queue.length > 0;
    if (queue.length === 1 && commonTitle) commonTitle.value = queue[0].title.value;
  };

  let nextFileId = 0;
  const addFiles = (files) => {
    const rejected = [];
    const categories = JSON.parse(document.querySelector("#category-options")?.textContent || "[]");
    Array.from(files).forEach((file) => {
      if (file.size > Number(dropZone.dataset.maxFileSize) * 1048576) {
        rejected.push(file.name);
        return;
      }
      const entry = { file };
      const row = document.createElement("article");
      row.className = "file-metadata-row";
      entry.row = row;
      const header = document.createElement("div");
      header.className = "file-metadata-header";
      const heading = document.createElement("h3");
      heading.id = `file-heading-${nextFileId}`;
      heading.textContent = file.name;
      row.setAttribute("aria-labelledby", heading.id);
      const size = document.createElement("span");
      size.className = "file-size";
      size.textContent = formatSize(file.size);
      header.append(heading, size);
      if (submittedFiles) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "button ghost remove-file";
        remove.textContent = "移除";
        remove.setAttribute("aria-label", `移除 ${file.name}`);
        remove.addEventListener("click", () => {
          const index = queue.indexOf(entry);
          queue.splice(index, 1);
          row.remove();
          syncQueue();
          (queue[index]?.row.querySelector("button") || queue[index - 1]?.row.querySelector("button") || fileInput).focus();
        });
        header.append(remove);
      }
      row.append(header);
      [
        { name: "title", label: "标题（可编辑）", type: "textarea", value: file.name.replace(/\.[^.]+$/, "").slice(0, 255) },
        { name: "description", label: "资料说明", type: "text" },
        { name: "category", label: "分类", type: "select" },
        { name: "tags", label: "标签", type: "text" },
        { name: "document_date", label: "资料日期", type: "date" },
      ].forEach((field) => {
        const controlId = `id_file_${nextFileId}_${field.name}`;
        const label = document.createElement("label");
        if (field.name === "title") label.className = "file-title-field";
        label.htmlFor = controlId;
        label.append(document.createTextNode(field.label));
        const control = document.createElement(["select", "textarea"].includes(field.type) ? field.type : "input");
        control.id = controlId;
        control.dataset.fileField = field.name;
        if (field.type === "select") {
          const empty = document.createElement("option");
          empty.value = "";
          empty.textContent = "使用公共分类";
          control.append(empty);
          categories.forEach((category) => {
            const option = document.createElement("option");
            option.value = category.id;
            option.textContent = category.name;
            control.append(option);
          });
        } else if (field.type === "textarea") {
          control.rows = 2;
          control.maxLength = 255;
          control.value = field.value;
          entry.title = control;
          control.addEventListener("input", () => {
            fitTitle(control);
            if (queue.length === 1 && commonTitle) commonTitle.value = control.value;
          });
        } else {
          control.type = field.type;
          if (field.name === "tags") control.maxLength = 500;
          if (field.type === "text") control.placeholder = "留空使用公共信息";
        }
        label.append(control);
        row.append(label);
      });
      fileMetadata.append(row);
      fitTitle(entry.title);
      queue.push(entry);
      nextFileId += 1;
    });
    selectionError.hidden = !rejected.length;
    selectionError.textContent = rejected.length ? `以下文件超过 ${dropZone.dataset.maxFileSize} MB，未加入列表：${rejected.join("；")}。其他文件已保留。` : "";
    syncQueue();
  };

  fileInput?.addEventListener("change", () => {
    if (!submittedFiles) {
      queue.splice(0);
      fileMetadata.replaceChildren();
    }
    addFiles(fileInput.files);
    if (submittedFiles) fileInput.value = "";
  });
  clearFiles?.addEventListener("click", () => {
    queue.splice(0);
    fileMetadata.replaceChildren();
    syncQueue();
    fileInput.focus();
  });
  window.addEventListener("resize", () => queue.forEach((entry) => fitTitle(entry.title)));
  if (submittedFiles) {
    syncQueue();
    let dragDepth = 0;
    const isFileDrag = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
    document.addEventListener("dragover", (event) => { if (isFileDrag(event)) event.preventDefault(); });
    document.addEventListener("drop", (event) => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      dragDepth = 0;
      dropZone.classList.remove("is-dragging");
    });
    dropZone.addEventListener("dragenter", (event) => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      dragDepth += 1;
      dropZone.classList.add("is-dragging");
    });
    dropZone.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) dropZone.classList.remove("is-dragging");
    });
    dropZone.addEventListener("dragover", (event) => {
      if (isFileDrag(event)) event.dataTransfer.dropEffect = "copy";
    });
    dropZone.addEventListener("drop", (event) => {
      if (isFileDrag(event)) addFiles(event.dataTransfer.files);
    });
  }

  const groupSet = document.querySelector("#upload-groups");
  if (groupSet) {
    const choices = Array.from(groupSet.querySelectorAll('input[type="checkbox"]'));
    const groupError = document.querySelector("#group-error");
    const validateGroups = () => {
      const valid = choices.some((choice) => choice.checked);
      choices[0]?.setCustomValidity(valid ? "" : "请至少选择一个所属项目/课题组。");
      if (valid) {
        groupError.hidden = true;
        choices.forEach((choice) => choice.removeAttribute("aria-invalid"));
      }
      return valid;
    };
    choices.forEach((choice) => {
      choice.setAttribute("aria-describedby", "group-help group-error");
      choice.addEventListener("change", validateGroups);
      choice.addEventListener("invalid", () => {
        groupError.textContent = "请至少选择一个所属项目/课题组。";
        groupError.hidden = false;
        choice.setAttribute("aria-invalid", "true");
      });
    });
    validateGroups();
    form.addEventListener("submit", (event) => {
      if (!validateGroups()) {
        event.preventDefault();
        groupError.textContent = "请至少选择一个所属项目/课题组。";
        groupError.hidden = false;
        groupSet.tabIndex = -1;
        groupSet.focus();
      }
    });
  }

  const updateRecordFields = () => {
    const isPublication = typeSelect?.value === "publication";
    if (publicationFields) publicationFields.hidden = !isPublication;
    const documentDate = document.querySelector('[data-field="document_date"]');
    if (documentDate) documentDate.hidden = isPublication;
  };
  typeSelect?.addEventListener("change", updateRecordFields);
  updateRecordFields();

  parseButton?.addEventListener("click", async () => {
    const bibtex = document.querySelector("#id_bibtex_raw");
    const csrf = form?.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    if (!bibtex?.value.trim()) {
      parseStatus.textContent = "请先粘贴 BibTeX 内容。";
      return;
    }
    parseButton.disabled = true;
    parseStatus.textContent = "正在解析…";
    try {
      const response = await fetch("/bibliography/parse/", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf },
        body: new URLSearchParams({ bibtex: bibtex.value, csrfmiddlewaretoken: csrf }),
        credentials: "same-origin",
      });
      const values = await response.json();
      if (!response.ok) throw new Error(values.error || "解析失败。");
      if (values.title && commonTitle) {
        commonTitle.value = values.title;
        if (queue.length === 1) {
          queue[0].title.value = values.title.slice(0, 255);
          fitTitle(queue[0].title);
        }
      }
      const fields = {
        authors: "authors",
        publication_date: "publication_date",
        publication_year: "publication_year",
        publication_month: "publication_month",
        publication_day: "publication_day",
        journal_name: "journal_name",
        volume: "volume",
        issue: "issue",
        pages: "pages",
        doi: "doi",
        url: "url",
      };
      Object.entries(fields).forEach(([source, target]) => {
        const input = document.querySelector(`#id_${target}`);
        if (input && values[source] !== null && values[source] !== undefined && values[source] !== "") input.value = values[source];
      });
      if (journalNameInput && values.journal_name) journalNameInput.value = values.journal_name;
      if (journalIdInput) journalIdInput.value = values.journal_id || "";
      if (journalNameInput && !values.journal_id && values.journal_name) matchJournal(values.journal_name);
      updateDateSummary();
      parseStatus.textContent = values.publication_date
        ? "已读取完整发表日期，并匹配期刊目录；请核对字段后提交。原始 BibTeX 会保留。"
        : values.publication_year
          ? "已读取发表年份和可识别月份；BibTeX 未提供完整日期时，日期会保留为空。请核对后提交。"
          : "字段已读取；请核对日期、期刊等信息后提交。原始 BibTeX 会保留。";
    } catch (error) {
      parseStatus.textContent = error.message;
    } finally {
      parseButton.disabled = false;
    }
  });
});
