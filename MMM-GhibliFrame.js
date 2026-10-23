/* MMM-GhibliFrame : cadre photo plein écran des images Ghibli (dossier photos/ -> ~/mm-ghibli/ghibli).
 * Pensé pour MMM-pages : le diaporama ne tourne que quand la page est affichée (suspend/resume),
 * une seule image est chargée à la fois (mémoire du Pi 5 limitée). */
Module.register("MMM-GhibliFrame", {
	defaults: {
		interval: 45 * 1000, // durée d'affichage d'une photo
		fade: 2000, // fondu enchaîné (ms)
		random: true
	},

	start() {
		this.files = [];
		this.index = 0;
		this.timer = null;
		this.front = 0;
		this.sendSocketNotification("GF_LIST", {});
	},

	getStyles() {
		return ["MMM-GhibliFrame.css"];
	},

	getDom() {
		const wrap = document.createElement("div");
		wrap.className = "ghibliframe";
		this.layers = [0, 1].map(() => {
			const d = document.createElement("div");
			d.className = "ghibliframe-layer";
			d.style.transition = `opacity ${this.config.fade}ms ease-in-out`;
			wrap.appendChild(d);
			return d;
		});
		return wrap;
	},

	socketNotificationReceived(notification, payload) {
		if (notification !== "GF_FILES") return;
		this.files = payload.files || [];
		if (this.config.random) this.files.sort(() => Math.random() - 0.5);
		this.index = 0;
		if (!this.hidden) this.run();
	},

	run() {
		this.stop();
		if (!this.files.length || !this.layers) return;
		this.showNext();
		this.timer = setInterval(() => this.showNext(), this.config.interval);
	},

	stop() {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
	},

	// not named show(): that would override Module.show() and MMM-pages could never reveal the module
	showNext() {
		const file = this.files[this.index % this.files.length];
		this.index++;
		const next = 1 - this.front;
		const img = new Image();
		img.onload = () => {
			this.layers[next].style.backgroundImage = `url("${img.src}")`;
			this.layers[next].style.opacity = 1;
			this.layers[this.front].style.opacity = 0;
			this.front = next;
		};
		img.src = `modules/${this.name}/photos/${encodeURIComponent(file)}`;
	},

	// MMM-pages masque/affiche le module à chaque changement de page
	suspend() {
		this.stop();
		if (this.layers) this.layers.forEach((l) => { l.style.backgroundImage = "none"; l.style.opacity = 0; });
	},

	resume() {
		this.sendSocketNotification("GF_LIST", {}); // relit le dossier (de nouvelles photos ont pu arriver pendant la nuit)
	}
});
