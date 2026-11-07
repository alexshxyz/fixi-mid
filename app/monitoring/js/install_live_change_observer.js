() => {
    const observerKey = "__fixi_live_table_observer";
    const changeFlag = "__fixi_live_table_changed";
    const table = document.querySelector('table#table_live');
    if (!table) {
        throw new Error('Live table not found.');
    }
    const observationRoot = document.documentElement;

    const belongsToLiveTable = (node) => {
        const element = node.nodeType === Node.ELEMENT_NODE
            ? node
            : node.parentElement;
        return Boolean(element && element.closest('table#table_live'));
    };
    const containsLiveTable = (node) => {
        const element = node.nodeType === Node.ELEMENT_NODE
            ? node
            : node.parentElement;
        return Boolean(
            element && (
                element.matches('table#table_live') ||
                element.querySelector('table#table_live')
            )
        );
    };

    window[changeFlag] = false;
    const observer = new MutationObserver((records) => {
        const liveTableChanged = records.some((record) => {
            if (belongsToLiveTable(record.target)) return true;
            return [...record.addedNodes, ...record.removedNodes].some(
                (node) => belongsToLiveTable(node) || containsLiveTable(node)
            );
        });
        if (liveTableChanged) window[changeFlag] = true;
    });
    observer.observe(observationRoot, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['class', 'style'],
    });
    window[observerKey] = observer;
}
