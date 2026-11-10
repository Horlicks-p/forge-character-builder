"""Custom Poses controls for the Pose Gallery tab.

The add form, details editor and delete confirmation live here so the legacy
gallery only needs to route to this module. User-written names are only ever
shown through plain-text components and gallery captions, never Markdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import gradio as gr

from character_builder_core import custom_poses, session
from character_builder_core.custom_poses import CustomPoseError, CustomPoseStore

CUSTOM_LABEL = "Custom Poses"
_REF_PREFIX = f"{CUSTOM_LABEL}:"

MSG_NOT_SELECTED = "No item selected"
MSG_SAVED_HIDDEN = "Pose saved but hidden by Safe Mode. Disable Safe Mode to view it."
MSG_NEEDS_FILE = "Choose an image first."
MSG_LINES_FADE = "It was reduced to fit the size limit, so thin lines may look fainter. An original of 2048 px or less keeps them sharp."

KIND_CHOICES = [(label, kind) for kind, label in custom_poses.INPUT_KIND_LABELS.items()]
UPLOAD_TYPES = [".png", ".jpg", ".jpeg", ".webp"]


def selection_ref(item_id: str) -> str:
    return f"{_REF_PREFIX}{item_id}"


def custom_id(ref: object) -> Optional[str]:
    """The item ID of a Custom Poses reference, else ``None``."""
    text = str(ref or "")
    return text[len(_REF_PREFIX):] if text.startswith(_REF_PREFIX) else None


def _no_angle():
    return gr.update(choices=[], value=None, visible=False, interactive=False)


def cleared_selection(details: str = MSG_NOT_SELECTED) -> tuple:
    """Same order as the legacy selection outputs."""
    return None, "", "", details, _no_angle()


def _unchanged_selection() -> tuple:
    return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()


def _details(item: custom_poses.CustomPose) -> str:
    return "\n".join(
        (
            item.name,
            f"ID: {item.id}",
            f"Type: {item.input_label}",
            f"Preprocessor: {item.preprocessor}",
            f"Added: {item.created_at}",
        )
    )


def select(store: CustomPoseStore, ref: object, safe_mode: bool) -> tuple:
    """Selection outputs for a pose, or cleared ones saying why it cannot be shown."""
    item_id = custom_id(ref)
    try:
        image = store.preview_image(item_id, safe_mode=bool(safe_mode))
    except CustomPoseError as error:
        return cleared_selection(str(error))
    item = store.get(item_id)
    if item is None:
        return cleared_selection(custom_poses.ERROR_NO_ITEM)
    return image, selection_ref(item.id), item.prompt, _details(item), _no_angle()


def reconcile(store: CustomPoseStore, ref: object, safe_mode: bool) -> tuple:
    """Clear a selection that was deleted or hidden since it was made."""
    item_id = custom_id(ref)
    if item_id is None:
        return _unchanged_selection()
    item = store.get(item_id)
    if item is None:
        return cleared_selection(custom_poses.ERROR_NO_ITEM)
    if safe_mode and item.sensitive:
        return cleared_selection(custom_poses.ERROR_HIDDEN)
    return _unchanged_selection()


def refresh(store: CustomPoseStore, query: str, safe_mode: bool, page: object) -> tuple:
    """Same order as the legacy gallery outputs."""
    result = session.custom_pose_gallery(store, query, safe_mode, page)
    hidden = gr.update(choices=["All"], value="All", visible=False)
    return result.images, [selection_ref(item_id) for item_id in result.ids], result.page, result.info, hidden, hidden, gr.update(visible=False)


@dataclass
class AddForm:
    group: Any
    file: Any
    preview: Any
    name: Any
    prompt: Any
    kind: Any
    sensitive: Any
    save: Any
    cancel: Any


@dataclass
class ManageControls:
    group: Any
    name: Any
    prompt: Any
    kind: Any
    sensitive: Any
    save: Any
    delete: Any
    confirm_group: Any
    confirm_text: Any
    confirm_yes: Any
    confirm_no: Any
    delete_target: Any


def build_add_form() -> AddForm:
    with gr.Group(visible=False, elem_classes=["fcb-custom-add"]) as group:
        with gr.Row():
            with gr.Column(scale=3):
                file = gr.File(label="Upload image", file_count="single", file_types=UPLOAD_TYPES, type="filepath", height=150, elem_id="fcb_custom_file")
                name = gr.Textbox(label="Name (optional)", placeholder="Defaults to the file name", max_lines=1, elem_id="fcb_custom_name")
                prompt = gr.Textbox(
                    label="Prompt (optional)",
                    placeholder="Filled into the Prompt box when this pose is selected",
                    lines=2,
                    elem_id="fcb_custom_prompt",
                )
                kind = gr.Dropdown(KIND_CHOICES, value=custom_poses.KIND_REFERENCE_CANNY, label="Input type", elem_id="fcb_custom_kind")
                sensitive = gr.Checkbox(
                    value=False,
                    label="Mark as sensitive",
                    info="A label you set for Safe Mode. Images are not checked automatically.",
                    elem_id="fcb_custom_sensitive",
                )
            with gr.Column(scale=2):
                preview = gr.Image(label="Preview", type="pil", height=300, interactive=False, show_download_button=False, elem_id="fcb_custom_preview")
        with gr.Row():
            save = gr.Button("Save Pose", variant="primary", elem_id="fcb_custom_save")
            cancel = gr.Button("Cancel", elem_id="fcb_custom_cancel")
    return AddForm(group, file, preview, name, prompt, kind, sensitive, save, cancel)


def build_manage_controls() -> ManageControls:
    delete_target = gr.State("")
    with gr.Group(visible=False, elem_classes=["fcb-custom-manage"]) as group:
        gr.Markdown(
            "Use a ControlNet model that matches the image type. A Preprocessed Canny map should be white lines on black; invert it first if it is black on white.",
            elem_classes=["fcb-help"],
        )
        name = gr.Textbox(label="Name", max_lines=1, elem_id="fcb_custom_edit_name")
        prompt = gr.Textbox(label="Saved prompt", lines=2, elem_id="fcb_custom_edit_prompt")
        kind = gr.Dropdown(KIND_CHOICES, value=custom_poses.KIND_REFERENCE_CANNY, label="Input type", elem_id="fcb_custom_edit_kind")
        sensitive = gr.Checkbox(value=False, label="Mark as sensitive", elem_id="fcb_custom_edit_sensitive")
        with gr.Row():
            save = gr.Button("Save details", elem_id="fcb_custom_edit_save")
            delete = gr.Button("Delete", variant="stop", elem_id="fcb_custom_delete")
        with gr.Group(visible=False) as confirm_group:
            confirm_text = gr.Textbox(label="Delete this pose?", interactive=False, lines=2, elem_id="fcb_custom_confirm_text")
            with gr.Row():
                confirm_yes = gr.Button("Delete permanently", variant="stop", elem_id="fcb_custom_confirm_yes")
                confirm_no = gr.Button("Keep it", elem_id="fcb_custom_confirm_no")
    return ManageControls(group, name, prompt, kind, sensitive, save, delete, confirm_group, confirm_text, confirm_yes, confirm_no, delete_target)


class CustomPoseEvents:
    """Binds the controls to the store and to the legacy gallery's outputs."""

    def __init__(
        self,
        store: CustomPoseStore,
        form: AddForm,
        manage: ManageControls,
        *,
        add_button: Any,
        status: Any,
        library: Any,
        search: Any,
        page: Any,
        safe_mode: Any,
        selected_ref: Any,
        selection_outputs: list,
        refresh_fn: Callable,
        refresh_inputs: list,
        refresh_outputs: list,
        non_custom_controls: list,
        source_status: Any,
        source_status_fn: Callable,
    ):
        self.store = store
        self.form = form
        self.manage = manage
        self.add_button = add_button
        self.status = status
        self.search = search
        self.page = page
        self.safe_mode = safe_mode
        self.selected_ref = selected_ref
        self.selection_outputs = selection_outputs
        self.refresh_fn = refresh_fn
        self.refresh_inputs = refresh_inputs
        self.refresh_outputs = refresh_outputs
        self.non_custom_controls = non_custom_controls
        self.source_status = source_status
        self.source_status_fn = source_status_fn
        self.manage_outputs = [
            manage.group,
            manage.name,
            manage.kind,
            manage.sensitive,
            manage.confirm_group,
            manage.confirm_text,
            manage.delete_target,
            manage.prompt,
        ]
        self.form_outputs = [form.group, form.file, form.preview, form.name, form.prompt, form.kind, form.sensitive]
        self._wire(library)

    # ---------------------------------------------------------- shared views

    def manage_view(self, ref, safe_mode):
        item = self.store.get(custom_id(ref))
        if item is None or (safe_mode and item.sensitive):
            return gr.update(visible=False), "", gr.update(), False, gr.update(visible=False), "", "", ""
        return gr.update(visible=True), item.name, item.input_kind, item.sensitive, gr.update(visible=False), "", "", item.prompt

    def mode_update(self, label):
        custom = label == CUSTOM_LABEL
        state = self.store.status()
        updates = {control: gr.update(visible=not custom) for control in self.non_custom_controls}
        updates[self.add_button] = gr.update(visible=custom, interactive=state.writable)
        updates[self.status] = gr.update(visible=custom, value="" if state.writable or not custom else state.message)
        updates[self.search] = gr.update(placeholder="Name or ID" if custom else "Category, type, or ID")
        updates.update(self._closed_form())
        return updates

    def _closed_form(self) -> dict:
        form = self.form
        return {
            form.group: gr.update(visible=False),
            form.file: None,
            form.preview: None,
            form.name: "",
            form.prompt: "",
            form.kind: custom_poses.KIND_REFERENCE_CANNY,
            form.sensitive: False,
        }

    def _selection(self, view: tuple) -> dict:
        return dict(zip(self.selection_outputs, view))

    # --------------------------------------------------------------- add form

    def preview_upload(self, file, kind):
        if not file:
            return None, ""
        try:
            return custom_poses.preview_upload(file, kind), ""
        except CustomPoseError as error:
            return None, str(error)

    def save_upload(self, file, name, prompt, kind, sensitive, safe_mode):
        if not file:
            return {self.status: MSG_NEEDS_FILE}
        try:
            result = self.store.add(file, name=name or "", input_kind=kind, sensitive=bool(sensitive), prompt=prompt or "")
        except CustomPoseError as error:
            return {self.status: str(error)}
        item = result.item
        hidden = safe_mode and item.sensitive
        if hidden:
            message = MSG_SAVED_HIDDEN
        elif result.duplicate:
            message = f"Already in the library: {item.name}"
        else:
            message = f"Saved: {item.name}"
            if result.lines_may_fade:
                message = f"{message}. {MSG_LINES_FADE}"
        view = cleared_selection() if hidden else select(self.store, selection_ref(item.id), safe_mode)
        updates = {**self._closed_form(), **self._selection(view), self.status: message}
        if hidden or not result.duplicate:  # a shown duplicate is selected where the gallery already is
            updates.update({self.search: "", self.page: 1})
        return updates

    # ---------------------------------------------------------------- manage

    def save_details(self, ref, name, prompt, kind, sensitive, safe_mode):
        try:
            item = self.store.update(custom_id(ref), name=name, prompt=prompt or "", input_kind=kind, sensitive=bool(sensitive))
        except CustomPoseError as error:
            if str(error) == custom_poses.ERROR_NO_ITEM:
                return {**self._selection(cleared_selection(str(error))), self.status: str(error)}
            return {self.status: str(error)}
        if safe_mode and item.sensitive:
            return {**self._selection(cleared_selection()), self.status: "Details saved. Safe Mode now hides this pose."}
        return {**self._selection(select(self.store, selection_ref(item.id), safe_mode)), self.status: f"Saved details: {item.name}"}

    def ask_delete(self, ref):
        item = self.store.get(custom_id(ref))
        if item is None:
            return {**self._selection(cleared_selection(custom_poses.ERROR_NO_ITEM)), self.status: custom_poses.ERROR_NO_ITEM}
        return {
            self.manage.confirm_group: gr.update(visible=True),
            self.manage.confirm_text: f"{item.name} will be permanently deleted. This cannot be undone.",
            self.manage.delete_target: item.id,
        }

    def cancel_delete(self):
        return gr.update(visible=False), "", ""

    def confirm_delete(self, target, ref=None, safe_mode=False):
        """Deletes the pose the dialog named, whatever is selected by now.

        Only that pose's selection is cleared: if the user picked another pose
        while the dialog was open, it stays selected (unless it went stale too).
        """
        deleted_selected = custom_id(ref) == target
        try:
            result = self.store.delete(target)
        except CustomPoseError as error:
            if str(error) == custom_poses.ERROR_NO_ITEM:
                view = cleared_selection(str(error)) if deleted_selected else reconcile(self.store, ref, safe_mode)
                return {**self._selection(view), self.status: str(error)}
            return {self.status: str(error)}
        message = f"Deleted: {result.item.name}"
        if result.orphans:
            message = f"{message}. Some files were in use and remain in the library folder."
        view = cleared_selection() if deleted_selected else reconcile(self.store, ref, safe_mode)
        return {**self._selection(view), self.status: message}

    def reconcile_selection(self, ref, safe_mode):
        return reconcile(self.store, ref, safe_mode)

    # ---------------------------------------------------------------- wiring

    def _finish(self, dependency):
        """After any library change: redraw the gallery, the editor and the source count."""
        dependency.then(self.source_status_fn, outputs=[self.source_status], show_progress=False)
        return dependency.then(self.refresh_fn, self.refresh_inputs, self.refresh_outputs, show_progress=False).then(
            self.manage_view, [self.selected_ref, self.safe_mode], self.manage_outputs, show_progress=False
        )

    def after_selection(self, dependency):
        """Sync the editor after an event that changed the selection."""
        return dependency.then(self.manage_view, [self.selected_ref, self.safe_mode], self.manage_outputs, show_progress=False)

    def after_send(self, dependency):
        """A send can fail because the pose is gone: drop the stale selection."""
        return self.after_selection(
            dependency.then(self.reconcile_selection, [self.selected_ref, self.safe_mode], self.selection_outputs, show_progress=False)
        )

    def _wire(self, library) -> None:
        form, manage = self.form, self.manage
        library.change(
            self.mode_update,
            [library],
            [*self.non_custom_controls, self.add_button, self.status, self.search, *self.form_outputs],
            show_progress=False,
        )

        self.add_button.click(lambda: gr.update(visible=True), outputs=[form.group], show_progress=False)
        form.cancel.click(lambda: (*self._closed_form().values(), ""), outputs=[*self.form_outputs, self.status], show_progress=False)
        form.file.upload(self.preview_upload, [form.file, form.kind], [form.preview, self.status], show_progress=False)
        form.file.clear(lambda: (None, ""), outputs=[form.preview, self.status], show_progress=False)
        form.kind.input(self.preview_upload, [form.file, form.kind], [form.preview, self.status], show_progress=False)
        self._finish(
            form.save.click(
                self.save_upload,
                [form.file, form.name, form.prompt, form.kind, form.sensitive, self.safe_mode],
                [*self.form_outputs, *self.selection_outputs, self.status, self.search, self.page],
                show_progress=False,
            )
        )

        editor_outputs = [*self.selection_outputs, self.status]
        self._finish(
            manage.save.click(
                self.save_details,
                [self.selected_ref, manage.name, manage.prompt, manage.kind, manage.sensitive, self.safe_mode],
                editor_outputs,
                show_progress=False,
            )
        )
        manage.delete.click(
            self.ask_delete,
            [self.selected_ref],
            [*self.selection_outputs, self.status, manage.confirm_group, manage.confirm_text, manage.delete_target],
            show_progress=False,
        )
        manage.confirm_no.click(self.cancel_delete, outputs=[manage.confirm_group, manage.confirm_text, manage.delete_target], show_progress=False)
        self._finish(
            manage.confirm_yes.click(
                self.confirm_delete, [manage.delete_target, self.selected_ref, self.safe_mode], editor_outputs, show_progress=False
            )
        )

        self.after_selection(
            self.safe_mode.change(self.reconcile_selection, [self.selected_ref, self.safe_mode], self.selection_outputs, show_progress=False)
        )
