function appendCharacterBuilderPrompt(text, tabname) {
    const prompt = String(text || "").trim();
    if (!prompt) {
        return;
    }

    const textarea = gradioApp().querySelector(`#${tabname}_prompt textarea`);
    if (!textarea) {
        return;
    }

    if (textarea.value.trim()) {
        const current = textarea.value.replace(/\s+$/, "");
        const separator = current.endsWith(",") ? " " : ", ";
        textarea.value = current + separator + prompt;
    } else {
        textarea.value = prompt;
    }

    updateInput(textarea);

    if (tabname === "txt2img") {
        switch_to_txt2img();
    } else if (tabname === "img2img") {
        switch_to_img2img();
    }
}
