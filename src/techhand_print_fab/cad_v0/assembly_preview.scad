// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Assembly preview — all parts posed for visual check.
// Training GRIP BLOCK + module bay — not a firearm silhouette.

/* ===== PARAMETERS ===== */
$fn = 48;
show_left = true;
show_right = true;
show_backstrap = true;
show_trigger = true;
show_spring_seat = true;
show_laser_clamp = true;
backstrap_size = 1;        // 0/1/2
laser_module_d = 12;

grip_len = 110;
grip_w = 32;
grip_h = 120;
half_w = grip_w / 2;

module part_left() {
    // Import left shell via include with side override — use separate render files
    // For preview, color and place; actual geometry from grip_shell with side=1
    color("SteelBlue", 0.85)
        translate([0, 0, 0])
            import_grip(1);
}

// OpenSCAD 2021 cannot easily re-parameterize includes; duplicate thin wrappers below
// via use of files that set side. We'll use children-less include pattern with files.

include <grip_shell_left.scad>;
