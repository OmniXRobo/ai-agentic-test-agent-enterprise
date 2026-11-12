document.querySelector("#allow").onclick = async () => {
  const out = document.querySelector("#out");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop()); // nothing is recorded here
    out.textContent = "Allowed. You can close this tab and use Speak in the side panel.";
  } catch (e) {
    out.textContent = "The microphone was not allowed (" + e.name + "). Your organisation's policy may block it; typing works the same way.";
  }
};
