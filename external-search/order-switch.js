/* external-search — result order switch and flat result list. Optional file: delete it to keep the core's list.
 *
 * The server tells the order of a result page in the list's search scope: "external" (grouped by folder),
 * "external:date" (newest first) or "external:relevance" (most relevant first). In engine order the folders are no
 * reading key, so:
 *   - the list is one flat list (the core's grouping by folder is turned off for these pages only);
 *   - every row is tagged with its folder name (the current folder included: in a mixed list it is one folder among
 *     others);
 *   - two buttons above the list switch between the two orders (admin setting "Show the order switch"). The choice
 *     is kept in the browser (localStorage "external-search-order"), and search.js sends it as externalOrder.
 * Relies on: the view model MailMessageList (rl-view-model.create event, listGrouped computed), rl.app.messageList
 * (listSearchScope, reload), rl.app.folderList, rl.pluginSettingsGet, rl.i18n. Re-check them after every
 * core upgrade: replacing listGrouped is the fragile part.
 */
(rl => {
	if (!rl) {
		return;
	}
	const ORDER_KEY = 'external-search-order',
		ORDERS = [['date', 'EXTERNAL_SEARCH/ORDER_DATE'], ['relevance', 'EXTERNAL_SEARCH/ORDER_RELEVANCE']],
		showSwitch = () => false !== rl.pluginSettingsGet?.('external-search', 'show_order_switch'),
		// 'date' | 'relevance' on a result page in engine order, '' otherwise
		pageOrder = () => {
			const s = rl.app?.messageList?.listSearchScope?.() || '';
			return s.startsWith('external:') ? s.slice(9) : '';
		},
		folderName = fullName => {
			const find = list => {
				for (const f of (list || [])) {
					if (f.fullName === fullName) {
						return f.name();
					}
					const r = find(f.subFolders && f.subFolders());
					if (r) {
						return r;
					}
				}
				return null;
			};
			return find(rl.app?.folderList?.()) || fullName;
		},
		tagRows = () => {
			const engine = !!pageOrder();
			document.querySelectorAll('#V-MailMessageList .messageListItem').forEach(row => {
				const m = window.ko?.dataFor(row), tag = row.querySelector('.external-search-folder');
				if (!engine || !m || !m.folder) {
					tag && tag.remove();
					return;
				}
				const text = folderName(m.folder);
				if (tag) {
					tag.textContent === text || (tag.textContent = text);
					return;
				}
				const t = document.createElement('span');
				t.className = 'external-search-folder';
				t.textContent = text;
				(row.querySelector('.senderParent') || row).before(t);
			});
		},
		placeSwitch = () => {
			const order = pageOrder(), host = document.querySelector('#V-MailMessageList .b-content');
			let bar = document.querySelector('.external-search-order');
			if (!order || !host || !showSwitch()) {
				bar && bar.remove();
				return;
			}
			if (!bar) {
				bar = document.createElement('div');
				bar.className = 'external-search-order';
				ORDERS.forEach(([id, label]) => {
					const b = document.createElement('button');
					b.type = 'button';
					b.dataset.order = id;
					b.textContent = rl.i18n(label);
					b.addEventListener('click', () => {
						if (id === pageOrder()) {
							return;
						}
						try {
							localStorage.setItem(ORDER_KEY, id);
						} catch (e) {
							return;
						}
						rl.app.messageList.reload(true);
					});
					bar.appendChild(b);
				});
				host.prepend(bar);
			}
			bar.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.order === order));
		},
		update = () => {
			tagRows();
			placeSwitch();
		};

	// Other scripts (a theme, another plugin) can see that this file handles the result order
	document.documentElement.classList.add('external-search-order-switch');

	addEventListener('rl-view-model.create', e => {
		const vm = e.detail;
		if ('MailMessageList' !== vm?.viewModelTemplateID || !window.ko || !window.ko.isObservable(vm.listGrouped)) {
			return;
		}
		const coreGrouped = vm.listGrouped;
		vm.listGrouped = window.ko.computed(() => pageOrder() ? false : coreGrouped());
	});

	addEventListener('rl-view-model', () => setTimeout(update, 0));
	let queued = false;
	const watch = () => new MutationObserver(() => {
		if (!queued) {
			queued = true;
			requestAnimationFrame(() => {
				queued = false;
				update();
			});
		}
	}).observe(document.getElementById('rl-right') || document.body, { childList: true, subtree: true });
	'loading' === document.readyState ? addEventListener('DOMContentLoaded', watch) : watch();
})(window.rl);
