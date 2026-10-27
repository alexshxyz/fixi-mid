() => {
    const observerKey = "__fixi_live_table_observer";
    const changeFlag = "__fixi_live_table_changed";
    window[observerKey]?.disconnect();
    delete window[observerKey];
    delete window[changeFlag];
}
