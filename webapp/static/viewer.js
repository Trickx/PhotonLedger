// Full-screen viewer for the versions of a target: click a thumbnail (or the cover image) to open it,
// click into the image to zoom 2x at that point, arrows / swipe for the next image, Esc to close.
(() => {
    const dlg = document.getElementById('pl-viewer');
    if (!dlg) return;
    const img = dlg.querySelector('img'), video = dlg.querySelector('video'), stage = dlg.querySelector('.pl-viewer-stage');
    const nameEl = dlg.querySelector('.pl-viewer-name'), infoEl = dlg.querySelector('.pl-viewer-info');
    const dl = dlg.querySelector('.pl-viewer-dl'), cover = dlg.querySelector('.pl-viewer-cover');
    let items = [], idx = 0;

    const show = i => {
        idx = (i + items.length) % items.length;
        const it = items[idx].dataset;
        stage.classList.remove('is-zoomed');
        video.pause();
        if (it.video) {                                 // videos play in the browser, no zoom
            img.hidden = true; img.removeAttribute('src');
            video.hidden = false; video.src = it.video; video.play().catch(() => {});
        } else {
            video.hidden = true; video.removeAttribute('src'); video.load();
            img.hidden = false; img.src = it.full; img.alt = it.name || '';
        }
        nameEl.textContent = it.name || ''; infoEl.textContent = it.info || '';
        dl.hidden = !it.download; if (it.download) dl.href = it.download;
        cover.hidden = !it.cover;
        cover.innerHTML = it.isCover ? '<i class="bi bi-star-fill"></i> Titelbild' : '<i class="bi bi-star"></i> Als Titelbild';
        cover.disabled = !!it.isCover;
        dlg.querySelectorAll('.pl-viewer-nav').forEach(b => b.hidden = items.length < 2);
    };
    const open = el => {
        items = [...document.querySelectorAll(`[data-pl-view="${el.dataset.plView}"]`)];
        dlg.showModal(); show(items.indexOf(el));
    };

    document.addEventListener('click', e => {
        const el = e.target.closest('[data-pl-view]') ||
                   (e.target.closest('[data-pl-open]') && document.getElementById(e.target.closest('[data-pl-open]').dataset.plOpen));
        if (!el || dlg.contains(e.target)) return;
        e.preventDefault(); open(el);
    });
    dlg.querySelector('.pl-viewer-prev').onclick = () => show(idx - 1);
    dlg.querySelector('.pl-viewer-next').onclick = () => show(idx + 1);
    dlg.querySelector('.pl-viewer-close').onclick = () => dlg.close();
    dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });   // click on the dark background
    dlg.addEventListener('close', () => { img.removeAttribute('src'); video.pause(); video.removeAttribute('src'); video.load(); });
    stage.addEventListener('click', e => {
        if (e.target === video) return;
        if (e.target !== img) return dlg.close();
        const r = img.getBoundingClientRect();
        img.style.transformOrigin = `${(e.clientX - r.left) / r.width * 100}% ${(e.clientY - r.top) / r.height * 100}%`;
        stage.classList.toggle('is-zoomed');
    });
    document.addEventListener('keydown', e => {
        if (!dlg.open) return;
        if (e.key === 'ArrowLeft') show(idx - 1);
        else if (e.key === 'ArrowRight') show(idx + 1);
    });
    let x0 = null;
    stage.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, {passive: true});
    stage.addEventListener('touchend', e => {
        if (x0 === null || stage.classList.contains('is-zoomed') || !video.hidden) return;
        const dx = e.changedTouches[0].clientX - x0; x0 = null;
        if (Math.abs(dx) > 50) show(idx + (dx < 0 ? 1 : -1));
    });
    cover.onclick = () => {
        const it = items[idx].dataset;
        if (!it.cover || !window.htmx) return;
        htmx.ajax('POST', it.cover, {target: '#detail', swap: 'innerHTML'}).then(() => {
            const el = document.getElementById(items[idx].id);
            if (el) { items = [...document.querySelectorAll(`[data-pl-view="${el.dataset.plView}"]`)]; show(items.indexOf(el)); }
        });
    };
})();
