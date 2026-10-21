(function () {
    "use strict";
    const button = document.getElementById("saveLiveTruckBan");
    const status = document.getElementById("liveTruckBanStatus");
    if (!button) return;
    button.addEventListener("click", function () {
        const body = {
            truck_ban_start: document.getElementById("liveTruckBanStart").value,
            truck_ban_end: document.getElementById("liveTruckBanEnd").value,
            truck_ban_start_2: document.getElementById("liveTruckBanStart2").value,
            truck_ban_end_2: document.getElementById("liveTruckBanEnd2").value
        };
        button.disabled = true;
        fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok || !payload.success) throw new Error(payload.error || "Could not save truck ban windows.");
                if (status) status.textContent = "Saved. Morning " + body.truck_ban_start + "–" + body.truck_ban_end +
                    "; afternoon " + body.truck_ban_start_2 + "–" + body.truck_ban_end_2 + ".";
            });
        }).catch(function (error) {
            if (status) status.textContent = error.message || "Could not save truck ban windows.";
        }).finally(function () { button.disabled = false; });
    });
})();
