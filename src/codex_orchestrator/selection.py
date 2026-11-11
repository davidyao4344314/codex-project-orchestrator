"""Small browser bridge for a selection-triggered explanation button."""

import streamlit.components.v2 as components

selection_menu = components.component(
    "design_selection",
    js="""
export default function({setTriggerValue}) {
    const button = document.createElement('button');
    button.textContent = 'Explain with AI';
    button.setAttribute('aria-label', 'Explain selected text with AI');
    Object.assign(button.style, {position:'fixed', zIndex:'99999', display:'none',
        background:'#285c46', color:'white', padding:'8px 12px', borderRadius:'8px',
        border:'0', cursor:'pointer'});
    document.body.appendChild(button);
    let text = '';
    const select = (event) => {
        if (button.contains(event.target)) return;
        const selection = window.getSelection();
        text = selection ? selection.toString().trim() : '';
        const anchor = selection?.anchorNode?.parentElement;
        if (!text || text.length > 4000 || !anchor?.closest('[data-testid="stMain"]')) {
            button.style.display = 'none'; return;
        }
        button.style.left = Math.max(8, Math.min(event.clientX, window.innerWidth-170))+'px';
        button.style.top = Math.max(8, Math.min(event.clientY+12, window.innerHeight-50))+'px';
        button.style.display = 'block';
    };
    button.onpointerdown = (event) => event.preventDefault();
    button.onclick = () => {setTriggerValue('selected', text); button.style.display='none';};
    document.addEventListener('pointerup', select);
    return () => {document.removeEventListener('pointerup', select); button.remove();};
}
""",
)
