/* external-search — client. Redirects the message list search (MessageList with a non-empty "search") to the
 * ExternalSearch action, which answers in the same shape. Everything else goes through untouched.
 * The only DOM change: a class (+ tooltip) on the search field for 5 s when the server fell back to the native search. */
(rl => {
	const Remote = rl.app && rl.app.Remote;
	if (!Remote || Remote.__externalSearchWrapped) {
		return;
	}
	Remote.__externalSearchWrapped = true;

	const original = Remote.request,
		FALLBACK_CLASS = 'external-search-fallback',
		// Per-browser choice of the result order ('date' | 'relevance'), written by whatever UI offers the switch;
		// absent = the order chosen by the administrator
		ORDER_KEY = 'external-search-order',
		order = () => {
			try {
				const v = localStorage.getItem(ORDER_KEY);
				return ['date', 'relevance'].includes(v) ? v : '';
			} catch (e) {
				return '';
			}
		},
		// Inverse of the core's b64EncodeJSONSafe: base64url of the percent-encoded JSON
		decodeParams = segment => {
			try {
				let b64 = segment.replace(/-/g, '+').replace(/_/g, '/');
				b64 += '='.repeat((4 - b64.length % 4) % 4);
				const bin = atob(b64);
				return JSON.parse(decodeURIComponent(Array.from(bin, c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
			} catch (e) {
				return null;
			}
		},
		// Mirrors QueryParser::isNativeToken (server side stays authoritative): state words, is:/in:/keyword:/larger:/smaller:
		// and raw IMAP keywords (FROM:x) are answered by the mail server, so skip the extra hop
		STATE_WORDS = ['unseen', 'seen', 'flagged', 'unflagged', 'answered', 'unanswered', 'deleted', 'undeleted'],
		STATE_PREFIXES = ['is:', 'in:', 'keyword:', 'larger:', 'smaller:'],
		// Upper-case IMAP keywords of the core, mirror of QueryParser::IMAP_KEYWORDS (lib/QueryParser.php); "RE: x" stays text
		IMAP_KEYWORDS = [
			'DATE', 'BODY', 'EMAIL', 'FROM', 'TO', 'SUBJECT', 'KEYWORD', 'IN',
			'SMALLER', 'LARGER', 'SINCE', 'ON', 'SENTON', 'SENTSINCE', 'SENTBEFORE', 'BEFORE',
			'OLDER', 'YOUNGER', 'HEADER', 'TO-ONLY', 'ATTACHMENT', 'FLAGGED', 'UNFLAGGED', 'SEEN',
			'UNSEEN', 'ANSWERED', 'UNANSWERED', 'DELETED', 'UNDELETED'
		],
		nativeOnly = search => search.trim().split(/\s+/).some(tok => {
			if (tok.startsWith('"')) {
				return false;
			}
			const lower = tok.toLowerCase();
			return STATE_WORDS.includes(lower) || STATE_PREFIXES.some(p => lower.startsWith(p)) || IMAP_KEYWORDS.some(k => tok.startsWith(k + ':'));
		}),
		flagFallback = () => {
			const field = document.querySelector('input.inputSearch');
			if (!field) {
				return;
			}
			field.classList.add(FALLBACK_CLASS);
			const oldTitle = field.getAttribute('title');
			field.title = rl.i18n('EXTERNAL_SEARCH/UNAVAILABLE');
			setTimeout(() => {
				field.classList.remove(FALLBACK_CLASS);
				oldTitle === null ? field.removeAttribute('title') : field.setAttribute('title', oldTitle);
			}, 5000);
		};

	Remote.request = function(sAction, fCallback, params, iTimeout, sGetAdd) {
		if ('MessageList' === sAction) {
			// The core cancels the previous list request by action name ('MessageList') only: cancel a pending
			// external search too, or its late answer could overwrite a newer list (new search, folder change).
			// 'reload' is the reason the core's list callback silently ignores.
			this.abort('PluginExternalSearch', 'reload');
			let p = params;
			if (typeof sGetAdd === 'string' && sGetAdd.startsWith('MessageList/')) {
				p = decodeParams(sGetAdd.split('/').pop());
			}
			if (p && typeof p.search === 'string' && p.search.trim() !== '' && !nativeOnly(p.search)) {
				const o = order();
				o && (p = Object.assign({}, p, { externalOrder: o }));
				return original.call(this, 'PluginExternalSearch', (iError, oData, bCached) => {
					oData && oData.ExternalSearchFallback && flagFallback();
					fCallback && fCallback(iError, oData, bCached);
				}, p, iTimeout, '');
			}
		}
		return original.apply(this, arguments);
	};
})(window.rl);
