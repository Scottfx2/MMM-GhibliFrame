const NodeHelper = require("node_helper");
const fs = require("node:fs");
const path = require("node:path");

module.exports = NodeHelper.create({
	socketNotificationReceived(notification) {
		if (notification !== "GF_LIST") return;
		const dir = path.join(__dirname, "photos"); // lien symbolique vers ~/mm-ghibli/ghibli
		let files = [];
		try {
			files = fs.readdirSync(dir).filter((f) => /\.jpe?g$/i.test(f) && !f.includes(".tmp"));
		} catch (e) {
			console.error("[MMM-GhibliFrame] dossier illisible :", dir, e.message);
		}
		this.sendSocketNotification("GF_FILES", { files });
	}
});
