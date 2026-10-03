const fileInputs = ["rb-file-a", "rb-file-b"];

function updateFileFormState() {
  const submit = document.querySelector("[data-submit-files]");
  if (!(submit instanceof HTMLButtonElement)) {
    return;
  }

  submit.disabled = !fileInputs.every((id) => {
    const input = document.getElementById(id);
    return input instanceof HTMLInputElement && Boolean(input.files?.length);
  });
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }

  const trigger = target.closest("button[data-file-trigger]");
  if (!(trigger instanceof HTMLButtonElement)) {
    return;
  }

  const input = document.getElementById(trigger.dataset.fileTrigger ?? "");
  if (input instanceof HTMLInputElement && input.type === "file") {
    input.click();
  }
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "file" || !input.id) {
    return;
  }

  const filename = document.querySelector(`[data-file-name-for="${CSS.escape(input.id)}"]`);
  if (filename instanceof HTMLElement) {
    filename.textContent = input.files?.[0]?.name ?? "No file selected";
  }

  updateFileFormState();
});

updateFileFormState();
