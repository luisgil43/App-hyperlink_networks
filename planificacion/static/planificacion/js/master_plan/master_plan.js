document.addEventListener(
  "DOMContentLoaded",
  function () {

    const CELL_WIDTH = 30;

    const COLLAPSED_COLUMN_WIDTH = 30;

    const MIN_COMPACT_ROW_HEIGHT = 30;

    const LAYER_HEIGHT = 20;

    const ROW_VERTICAL_PADDING = 4;

    const COLUMN_STORAGE_KEY =
      "planning_master_plan_collapsed_columns_v1";

    const PAGE_SCROLL_STORAGE_KEY =
      "planning_master_plan_page_scroll_v1";


    const timelineMonthsHeader =
      document.getElementById(
        "timeline-months-header"
      );

    const timelineHeaderTitle =
      document.getElementById(
        "timeline-header-title"
      );

    const timelineDaysHeader =
      document.getElementById(
        "timeline-days-header"
      );

    const timelineTable =
      document.getElementById(
        "planning-timeline-table"
      );

    const timelineScroll =
      document.getElementById(
        "planning-timeline-scroll"
      );

    const timelineViewport =
      document.getElementById(
        "planning-timeline-viewport"
      );

    const fixedPane =
      document.getElementById(
        "planning-fixed-pane"
      );

    const fixedTable =
      document.getElementById(
        "planning-fixed-table"
      );

    const fixedHeaderRow =
      document.getElementById(
        "planning-fixed-header-row"
      );

    const fromWeekInput =
      document.getElementById(
        "timeline-from-week"
      );

    const toWeekInput =
      document.getElementById(
        "timeline-to-week"
      );

    const applyButton =
      document.getElementById(
        "timeline-apply"
      );

    const filterForm =
      document.getElementById(
        "master-plan-filter-form"
      );


    /* =========================================================
       CURRENT TIMELINE MODE
    ========================================================== */

    let timelineMode =
      "auto";


    /* =========================================================
       PAGE POSITION
    ========================================================== */

    function savePagePosition() {

      try {

        sessionStorage.setItem(
          PAGE_SCROLL_STORAGE_KEY,
          String(
            window.scrollY
          )
        );

      } catch (error) {
      }

    }


    function restorePagePosition() {

      let storedPosition = null;

      try {

        storedPosition =
          sessionStorage.getItem(
            PAGE_SCROLL_STORAGE_KEY
          );

        sessionStorage.removeItem(
          PAGE_SCROLL_STORAGE_KEY
        );

      } catch (error) {
        return;
      }


      if (
        storedPosition === null
      ) {
        return;
      }


      const position =
        Number(
          storedPosition
        );


      if (
        !Number.isFinite(
          position
        )
      ) {
        return;
      }


      window.requestAnimationFrame(
        function () {

          window.scrollTo(
            {
              top: position,
              left: 0,
              behavior: "auto",
            }
          );

        }
      );

    }


    document.querySelectorAll(
      "[data-preserve-master-scroll='1']"
    ).forEach(
      function (link) {

        link.addEventListener(
          "click",
          function () {

            savePagePosition();

          }
        );

      }
    );


    if (
      filterForm
    ) {

      filterForm.addEventListener(
        "submit",
        function () {

          savePagePosition();

        }
      );

    }


    restorePagePosition();


    /* =========================================================
       KEEP POSITION FOR LOCAL BUTTONS
    ========================================================== */

    function preservePosition(
      callback
    ) {

      const currentY =
        window.scrollY;

      callback();

      window.requestAnimationFrame(
        function () {

          window.scrollTo(
            {
              top: currentY,
              left: 0,
              behavior: "auto",
            }
          );

        }
      );

    }


    /* =========================================================
       DATE HELPERS
    ========================================================== */

    function normalizeDate(date) {

      const normalized =
        new Date(
          date
        );

      normalized.setHours(
        12,
        0,
        0,
        0
      );

      return normalized;

    }


    function addDays(
      date,
      days
    ) {

      const result =
        new Date(
          date
        );

      result.setDate(
        result.getDate()
        +
        days
      );

      return normalizeDate(
        result
      );

    }


    function dateKey(date) {

      const year =
        date.getFullYear();

      const month =
        String(
          date.getMonth() + 1
        ).padStart(
          2,
          "0"
        );

      const day =
        String(
          date.getDate()
        ).padStart(
          2,
          "0"
        );

      return (
        year
        +
        "-"
        +
        month
        +
        "-"
        +
        day
      );

    }


    function parseDate(value) {

      if (
        !value
      ) {
        return null;
      }


      const parts =
        value.split(
          "-"
        );


      if (
        parts.length !== 3
      ) {
        return null;
      }


      return normalizeDate(
        new Date(
          Number(
            parts[0]
          ),
          Number(
            parts[1]
          )
          -
          1,
          Number(
            parts[2]
          )
        )
      );

    }


    function parseWorkingDays(value) {

      if (
        !value
      ) {

        return new Set(
          [
            0,
            1,
            2,
            3,
            4,
            5,
            6,
          ]
        );

      }


      const result =
        new Set();


      value.split(
        ","
      ).forEach(
        function (item) {

          const day =
            Number(
              item
            );


          if (
            Number.isInteger(
              day
            )
            &&
            day >= 0
            &&
            day <= 6
          ) {

            result.add(
              day
            );

          }

        }
      );


      return result;

    }


    /* =========================================================
       ISO WEEK HELPERS
    ========================================================== */

    function getISOWeek(date) {

      const temp =
        new Date(
          Date.UTC(
            date.getFullYear(),
            date.getMonth(),
            date.getDate()
          )
        );


      const dayNumber =
        temp.getUTCDay()
        ||
        7;


      temp.setUTCDate(
        temp.getUTCDate()
        +
        4
        -
        dayNumber
      );


      const yearStart =
        new Date(
          Date.UTC(
            temp.getUTCFullYear(),
            0,
            1
          )
        );


      const weekNumber =
        Math.ceil(
          (
            (
              temp
              -
              yearStart
            )
            /
            86400000
            +
            1
          )
          /
          7
        );


      return {
        year: temp.getUTCFullYear(),
        week: weekNumber,
      };

    }


    function getMonday(date) {

      const result =
        normalizeDate(
          date
        );


      const day =
        result.getDay()
        ||
        7;


      result.setDate(
        result.getDate()
        -
        day
        +
        1
      );


      return normalizeDate(
        result
      );

    }


    function getSunday(date) {

      return addDays(
        getMonday(
          date
        ),
        6
      );

    }


    function isoWeekToMonday(value) {

      if (
        !value
      ) {
        return null;
      }


      const match =
        value.match(
          /^(\d{4})-W(\d{2})$/
        );


      if (
        !match
      ) {
        return null;
      }


      const year =
        Number(
          match[1]
        );


      const week =
        Number(
          match[2]
        );


      const januaryFourth =
        new Date(
          year,
          0,
          4
        );


      const firstMonday =
        getMonday(
          januaryFourth
        );


      return addDays(
        firstMonday,
        (
          week
          -
          1
        )
        *
        7
      );

    }


    function dateToWeekInput(date) {

      const iso =
        getISOWeek(
          date
        );


      return (
        iso.year
        +
        "-W"
        +
        String(
          iso.week
        ).padStart(
          2,
          "0"
        )
      );

    }


    /* =========================================================
       TODAY
    ========================================================== */

    const today =
      normalizeDate(
        new Date()
      );


    const currentMonday =
      getMonday(
        today
      );


    if (
      fromWeekInput
      &&
      !fromWeekInput.value
    ) {

      fromWeekInput.value =
        dateToWeekInput(
          currentMonday
        );

    }


    if (
      toWeekInput
      &&
      !toWeekInput.value
    ) {

      toWeekInput.value =
        dateToWeekInput(
          currentMonday
        );

    }


    /* =========================================================
       COLLAPSIBLE FIXED COLUMNS
    ========================================================== */

    function loadCollapsedColumns() {

      try {

        const value =
          localStorage.getItem(
            COLUMN_STORAGE_KEY
          );


        if (
          !value
        ) {
          return new Set();
        }


        const parsed =
          JSON.parse(
            value
          );


        if (
          !Array.isArray(
            parsed
          )
        ) {
          return new Set();
        }


        return new Set(
          parsed
        );

      } catch (error) {

        return new Set();

      }

    }


    let collapsedColumns =
      loadCollapsedColumns();


    function saveCollapsedColumns() {

      try {

        localStorage.setItem(
          COLUMN_STORAGE_KEY,
          JSON.stringify(
            Array.from(
              collapsedColumns
            )
          )
        );

      } catch (error) {
      }

    }


    function updateFixedPaneWidth() {

      if (
        !fixedPane
        ||
        !fixedTable
      ) {
        return;
      }


      let totalWidth =
        0;


      fixedTable.querySelectorAll(
        "col[data-column-col]"
      ).forEach(
        function (col) {

          const column =
            col.dataset.columnCol;


          const expandedWidth =
            Number(
              col.dataset.expandedWidth
            )
            ||
            COLLAPSED_COLUMN_WIDTH;


          const width =
            collapsedColumns.has(
              column
            )
              ?
              COLLAPSED_COLUMN_WIDTH
              :
              expandedWidth;


          col.style.width =
            width
            +
            "px";


          totalWidth +=
            width;

        }
      );


      fixedTable.style.width =
        totalWidth
        +
        "px";


      fixedTable.style.minWidth =
        totalWidth
        +
        "px";


      fixedTable.style.maxWidth =
        totalWidth
        +
        "px";


      fixedPane.style.width =
        totalWidth
        +
        "px";


      fixedPane.style.minWidth =
        totalWidth
        +
        "px";


      fixedPane.style.maxWidth =
        totalWidth
        +
        "px";

    }


    function renderColumnState(column) {

      const collapsed =
        collapsedColumns.has(
          column
        );


      document.querySelectorAll(
        "[data-column-content='"
        +
        column
        +
        "']"
      ).forEach(
        function (element) {

          if (
            collapsed
          ) {

            element.classList.add(
              "hidden"
            );

          } else {

            element.classList.remove(
              "hidden"
            );

          }

        }
      );


      document.querySelectorAll(
        "[data-column-label='"
        +
        column
        +
        "']"
      ).forEach(
        function (element) {

          if (
            collapsed
          ) {

            element.classList.add(
              "hidden"
            );

          } else {

            element.classList.remove(
              "hidden"
            );

          }

        }
      );


      document.querySelectorAll(
        ".planning-column-toggle[data-column='"
        +
        column
        +
        "']"
      ).forEach(
        function (button) {

          button.textContent =
            collapsed
              ?
              "+"
              :
              "−";


          button.title =
            collapsed
              ?
              "Expand column"
              :
              "Collapse column";


          if (
            collapsed
          ) {

            button.classList.add(
              "mx-auto"
            );

          } else {

            button.classList.remove(
              "mx-auto"
            );

          }

        }
      );


      document.querySelectorAll(
        "[data-column-cell='"
        +
        column
        +
        "']"
      ).forEach(
        function (cell) {

          if (
            collapsed
          ) {

            cell.classList.remove(
              "px-2"
            );

            cell.classList.add(
              "px-1"
            );

          } else {

            cell.classList.remove(
              "px-1"
            );

            cell.classList.add(
              "px-2"
            );

          }

        }
      );

    }


    function renderAllColumnStates() {

      if (
        !fixedTable
      ) {
        return;
      }


      fixedTable.querySelectorAll(
        "col[data-column-col]"
      ).forEach(
        function (col) {

          renderColumnState(
            col.dataset.columnCol
          );

        }
      );


      updateFixedPaneWidth();

    }


    /* =========================================================
       ACTIVITY DETAILS EXPAND / COLLAPSE
    ========================================================== */

    function setActivityDetailsState(
      button,
      expanded
    ) {

      if (
        !button
      ) {
        return;
      }


      const rowIndex =
        button.getAttribute(
          "data-row-index"
        );


      if (
        rowIndex === null
      ) {
        return;
      }


      const fixedRow =
        document.querySelector(
          ".planning-activity-row-fixed[data-row-index='"
          +
          rowIndex
          +
          "']"
        );


      if (
        !fixedRow
      ) {
        return;
      }


      const details =
        fixedRow.querySelector(
          ".planning-activity-details"
        );


      const arrow =
        button.querySelector(
          ".planning-activity-details-arrow"
        );


      if (
        !details
      ) {
        return;
      }


      if (
        expanded
      ) {

        details.classList.remove(
          "hidden"
        );


        button.setAttribute(
          "aria-expanded",
          "true"
        );


        button.title =
          "Hide activity details";


        if (
          arrow
        ) {

          arrow.textContent =
            "▾";

        }

      } else {

        details.classList.add(
          "hidden"
        );


        button.setAttribute(
          "aria-expanded",
          "false"
        );


        button.title =
          "Show activity details";


        if (
          arrow
        ) {

          arrow.textContent =
            "▸";

        }

      }


      window.requestAnimationFrame(
        function () {

          scheduleMatrixHeightSync();

        }
      );

    }


    document.addEventListener(
      "click",
      function (event) {

        const button =
          event.target.closest(
            ".planning-activity-details-toggle"
          );


        if (
          !button
        ) {
          return;
        }


        event.preventDefault();

        event.stopPropagation();


        const expanded =
          button.getAttribute(
            "aria-expanded"
          )
          ===
          "true";


        const currentY =
          window.scrollY;


        setActivityDetailsState(
          button,
          !expanded
        );


        window.requestAnimationFrame(
          function () {

            window.scrollTo(
              {
                top: currentY,
                left: 0,
                behavior: "auto",
              }
            );

          }
        );

      }
    );


    /* =========================================================
       VISIBLE TIMELINE LAYERS
    ========================================================== */

    function getVisibleTimelineLayerCount(
      timelineRow
    ) {

      if (
        !timelineRow
      ) {
        return 0;
      }


      let visibleLayers =
        0;


      timelineRow.querySelectorAll(
        ".planning-layer-row"
      ).forEach(
        function (layerRow) {

          if (
            layerRow.dataset.hasData
            ===
            "1"
            &&
            !layerRow.classList.contains(
              "hidden"
            )
          ) {

            visibleLayers +=
              1;

          }

        }
      );


      return visibleLayers;

    }


    function getDynamicMinimumRowHeight(
      timelineRow
    ) {

      const visibleLayerCount =
        getVisibleTimelineLayerCount(
          timelineRow
        );


      if (
        visibleLayerCount <= 0
      ) {

        return MIN_COMPACT_ROW_HEIGHT;

      }


      return Math.max(
        MIN_COMPACT_ROW_HEIGHT,
        (
          visibleLayerCount
          *
          LAYER_HEIGHT
        )
        +
        ROW_VERTICAL_PADDING
      );

    }


    /* =========================================================
       SYNCHRONIZE FIXED TABLE + TIMELINE HEIGHTS
    ========================================================== */

    function synchronizeMatrixHeights() {

      if (
        !fixedTable
        ||
        !timelineTable
      ) {
        return;
      }


      const timelineHead =
        timelineTable.querySelector(
          "thead"
        );


      if (
        fixedHeaderRow
        &&
        timelineHead
      ) {

        fixedHeaderRow.style.height =
          "";


        fixedHeaderRow.querySelectorAll(
          "th"
        ).forEach(
          function (cell) {

            cell.style.height =
              "";

          }
        );


        const timelineHeaderHeight =
          Math.ceil(
            timelineHead.getBoundingClientRect().height
          );


        fixedHeaderRow.style.height =
          timelineHeaderHeight
          +
          "px";


        fixedHeaderRow.querySelectorAll(
          "th"
        ).forEach(
          function (cell) {

            cell.style.height =
              timelineHeaderHeight
              +
              "px";

          }
        );

      }


      document.querySelectorAll(
        ".planning-activity-row-fixed"
      ).forEach(
        function (fixedRow) {

          const rowIndex =
            fixedRow.dataset.rowIndex;


          const timelineRow =
            document.querySelector(
              ".planning-timeline-row[data-row-index='"
              +
              rowIndex
              +
              "']"
            );


          if (
            !timelineRow
          ) {
            return;
          }


          const timelineContainer =
            timelineRow.querySelector(
              ".planning-timeline-container"
            );


          const wrapper =
            timelineRow.querySelector(
              ".planning-layer-grid"
            );


          const background =
            timelineRow.querySelector(
              ".planning-calendar-background"
            );


          fixedRow.style.height =
            "";


          timelineRow.style.height =
            "";


          fixedRow.querySelectorAll(
            "td"
          ).forEach(
            function (cell) {

              cell.style.height =
                "";

            }
          );


          timelineRow.querySelectorAll(
            "td"
          ).forEach(
            function (cell) {

              cell.style.height =
                "";

            }
          );


          if (
            wrapper
          ) {

            wrapper.style.height =
              "";

            wrapper.style.minHeight =
              "";

          }


          if (
            timelineContainer
          ) {

            timelineContainer.style.height =
              "";

          }


          if (
            background
          ) {

            background.style.height =
              "";

          }


          if (
            fixedRow.classList.contains(
              "hidden"
            )
            ||
            timelineRow.classList.contains(
              "hidden"
            )
          ) {
            return;
          }


          const fixedHeight =
            Math.ceil(
              fixedRow.getBoundingClientRect().height
            );


          const timelineHeight =
            Math.ceil(
              timelineRow.getBoundingClientRect().height
            );


          const dynamicMinimum =
            getDynamicMinimumRowHeight(
              timelineRow
            );


          const rowHeight =
            Math.max(
              dynamicMinimum,
              fixedHeight,
              timelineHeight
            );


          fixedRow.style.height =
            rowHeight
            +
            "px";


          timelineRow.style.height =
            rowHeight
            +
            "px";


          fixedRow.querySelectorAll(
            "td"
          ).forEach(
            function (cell) {

              cell.style.height =
                rowHeight
                +
                "px";

            }
          );


          timelineRow.querySelectorAll(
            "td"
          ).forEach(
            function (cell) {

              cell.style.height =
                rowHeight
                +
                "px";

            }
          );


          if (
            timelineContainer
          ) {

            timelineContainer.style.height =
              rowHeight
              +
              "px";

          }


          if (
            wrapper
          ) {

            wrapper.style.height =
              rowHeight
              +
              "px";

            wrapper.style.minHeight =
              rowHeight
              +
              "px";

          }


          if (
            background
          ) {

            background.style.height =
              rowHeight
              +
              "px";

          }

        }
      );

    }


    function scheduleMatrixHeightSync() {

      window.requestAnimationFrame(
        function () {

          synchronizeMatrixHeights();


          window.requestAnimationFrame(
            function () {

              synchronizeMatrixHeights();

            }
          );

        }
      );

    }


    document.addEventListener(
      "master-plan-layers-changed",
      function () {

        scheduleMatrixHeightSync();

      }
    );


    document.querySelectorAll(
      ".planning-column-toggle"
    ).forEach(
      function (button) {

        button.addEventListener(
          "click",
          function () {

            preservePosition(
              function () {

                const column =
                  button.dataset.column;


                if (
                  collapsedColumns.has(
                    column
                  )
                ) {

                  collapsedColumns.delete(
                    column
                  );

                } else {

                  collapsedColumns.add(
                    column
                  );

                }


                saveCollapsedColumns();


                renderColumnState(
                  column
                );


                updateFixedPaneWidth();


                scheduleMatrixHeightSync();

              }
            );

          }
        );

      }
    );


    renderAllColumnStates();


    /* =========================================================
       PLANNING BOUNDS FOR AUTOMATIC MODE
    ========================================================== */

    function getAutomaticPlanningBounds() {

      const dates =
        [];


      document.querySelectorAll(
        ".planning-timeline-row:not(.hidden) .planning-timeline-container"
      ).forEach(
        function (container) {

          [
            container.dataset.clientStart,
            container.dataset.clientFinish,
            container.dataset.planStart,
            container.dataset.planFinish,
          ].forEach(
            function (value) {

              const date =
                parseDate(
                  value
                );


              if (
                date
              ) {

                dates.push(
                  date
                );

              }

            }
          );

        }
      );


      if (
        dates.length === 0
      ) {

        return {
          start: currentMonday,
          end: getSunday(
            currentMonday
          ),
        };

      }


      const earliest =
        new Date(
          Math.min.apply(
            null,
            dates.map(
              function (date) {

                return date.getTime();

              }
            )
          )
        );


      const latest =
        new Date(
          Math.max.apply(
            null,
            dates.map(
              function (date) {

                return date.getTime();

              }
            )
          )
        );


      const firstPlanningWeek =
        getMonday(
          earliest
        );


      const lastPlanningWeek =
        getMonday(
          latest
        );


      return {
        start: addDays(
          firstPlanningWeek,
          -7
        ),
        end: addDays(
          lastPlanningWeek,
          13
        ),
      };

    }


    /* =========================================================
       MONTH HEADER
    ========================================================== */

    function buildMonthHeader(
      dates
    ) {

      if (
        !timelineMonthsHeader
      ) {
        return;
      }


      timelineMonthsHeader.innerHTML =
        "";


      if (
        dates.length === 0
      ) {
        return;
      }


      let groupStart =
        0;


      while (
        groupStart
        <
        dates.length
      ) {

        const firstDate =
          dates[
            groupStart
          ];


        const month =
          firstDate.getMonth();


        const year =
          firstDate.getFullYear();


        let groupEnd =
          groupStart
          +
          1;


        while (
          groupEnd
          <
          dates.length
          &&
          dates[
            groupEnd
          ].getMonth()
          ===
          month
          &&
          dates[
            groupEnd
          ].getFullYear()
          ===
          year
        ) {

          groupEnd +=
            1;

        }


        const span =
          groupEnd
          -
          groupStart;


        const th =
          document.createElement(
            "th"
          );


        th.colSpan =
          span;


        th.style.width =
          (
            span
            *
            CELL_WIDTH
          )
          +
          "px";


        th.style.minWidth =
          th.style.width;


        th.style.maxWidth =
          th.style.width;


        th.className =
          "h-[22px] px-2 text-center text-[10px] font-extrabold bg-blue-100 text-blue-800 border-r border-b border-blue-200 whitespace-nowrap";


        th.textContent =
          firstDate.toLocaleDateString(
            "en-US",
            {
              month: "long",
              year: "numeric",
            }
          );


        timelineMonthsHeader.appendChild(
          th
        );


        groupStart =
          groupEnd;

      }

    }


    /* =========================================================
       BUILD TIMELINE HEADER
    ========================================================== */

    function buildTimelineHeader(
      startDate,
      endDate
    ) {

      if (
        !timelineDaysHeader
      ) {
        return [];
      }


      timelineDaysHeader.innerHTML =
        "";


      const dates =
        [];


      let cursor =
        normalizeDate(
          startDate
        );


      while (
        cursor <= endDate
      ) {

        dates.push(
          normalizeDate(
            cursor
          )
        );


        cursor =
          addDays(
            cursor,
            1
          );

      }


      buildMonthHeader(
        dates
      );


      if (
        timelineHeaderTitle
      ) {

        timelineHeaderTitle.colSpan =
          dates.length;

      }


      dates.forEach(
        function (date) {

          const iso =
            getISOWeek(
              date
            );


          const isMonday =
            date.getDay()
            ===
            1;


          const isSunday =
            date.getDay()
            ===
            0;


          const th =
            document.createElement(
              "th"
            );


          th.style.width =
            CELL_WIDTH
            +
            "px";


          th.style.minWidth =
            CELL_WIDTH
            +
            "px";


          th.style.maxWidth =
            CELL_WIDTH
            +
            "px";


          th.className =
            "border-r border-b border-gray-200 text-center px-0 py-0";


          const weekLabel =
            isMonday
              ?
              (
                "<div class='bg-blue-50 text-blue-700 font-extrabold text-[9px] py-1 border-b border-blue-100'>"
                +
                "W"
                +
                String(
                  iso.week
                ).padStart(
                  2,
                  "0"
                )
                +
                "</div>"
              )
              :
              (
                "<div class='h-[21px] bg-blue-50 border-b border-blue-100'></div>"
              );


          const weekday =
            date.toLocaleDateString(
              "en-US",
              {
                weekday: "short",
              }
            ).slice(
              0,
              2
            );


          th.innerHTML =
            weekLabel
            +
            "<div class='"
            +
            (
              isSunday
                ?
                "bg-gray-100"
                :
                "bg-white"
            )
            +
            " py-1'>"
            +
            "<div class='font-bold text-[9px] text-gray-500'>"
            +
            weekday
            +
            "</div>"
            +
            "<div class='font-extrabold text-[10px] text-gray-700'>"
            +
            String(
              date.getDate()
            ).padStart(
              2,
              "0"
            )
            +
            "</div>"
            +
            "</div>";


          if (
            isMonday
          ) {

            th.classList.add(
              "border-l-2",
              "border-l-blue-200"
            );

          }


          timelineDaysHeader.appendChild(
            th
          );

        }
      );


      const timelineWidth =
        dates.length
        *
        CELL_WIDTH;


      if (
        timelineTable
      ) {

        timelineTable.style.width =
          timelineWidth
          +
          "px";


        timelineTable.style.minWidth =
          timelineWidth
          +
          "px";


        timelineTable.style.maxWidth =
          timelineWidth
          +
          "px";

      }


      return dates;

    }


    /* =========================================================
       TIMELINE BACKGROUND CELL
    ========================================================== */

    function createTimelineBackgroundCell(
      date
    ) {

      const cell =
        document.createElement(
          "div"
        );


      cell.style.width =
        CELL_WIDTH
        +
        "px";


      cell.style.minWidth =
        CELL_WIDTH
        +
        "px";


      cell.style.maxWidth =
        CELL_WIDTH
        +
        "px";


      cell.style.height =
        "100%";


      cell.className =
        "border-r border-gray-100";


      if (
        date.getDay()
        ===
        0
      ) {

        cell.classList.add(
          "bg-gray-50"
        );

      } else {

        cell.classList.add(
          "bg-white"
        );

      }


      if (
        date.getDay()
        ===
        1
      ) {

        cell.classList.add(
          "border-l-2",
          "border-l-blue-100"
        );

      }


      return cell;

    }


    /* =========================================================
       LAYER CELL
    ========================================================== */

    function createLayerCell(
      date,
      startDate,
      finishDate,
      layer,
      workingDays
    ) {

      const cell =
        document.createElement(
          "div"
        );


      cell.style.width =
        CELL_WIDTH
        +
        "px";


      cell.style.minWidth =
        CELL_WIDTH
        +
        "px";


      cell.style.maxWidth =
        CELL_WIDTH
        +
        "px";


      cell.className =
        "h-5 relative";


      const insideRange =
        (
          startDate
          &&
          finishDate
          &&
          date >= startDate
          &&
          date <= finishDate
        );


      let shouldDraw =
        insideRange;


      if (
        layer === "plan"
        &&
        shouldDraw
        &&
        workingDays
        &&
        !workingDays.has(
          date.getDay()
        )
      ) {

        shouldDraw =
          false;

      }


      if (
        shouldDraw
      ) {

        const bar =
          document.createElement(
            "div"
          );


        bar.className =
          "absolute inset-y-1 left-0 right-0";


        if (
          layer === "baseline"
        ) {

          bar.classList.add(
            "bg-emerald-200",
            "border-y",
            "border-emerald-300"
          );

        } else if (
          layer === "plan"
        ) {

          bar.classList.add(
            "bg-blue-500"
          );

        } else if (
          layer === "real"
        ) {

          bar.classList.add(
            "bg-amber-400"
          );

        }


        if (
          dateKey(
            date
          )
          ===
          dateKey(
            startDate
          )
        ) {

          bar.classList.add(
            "rounded-l-md"
          );

        }


        if (
          dateKey(
            date
          )
          ===
          dateKey(
            finishDate
          )
        ) {

          bar.classList.add(
            "rounded-r-md"
          );

        }


        cell.appendChild(
          bar
        );

      }


      return cell;

    }


    /* =========================================================
       BUILD ACTIVITY TIMELINES
    ========================================================== */

    function buildActivityTimelines(
      dates
    ) {

      const containers =
        document.querySelectorAll(
          ".planning-timeline-container"
        );


      containers.forEach(
        function (container) {

          const clientStart =
            parseDate(
              container.dataset.clientStart
            );


          const clientFinish =
            parseDate(
              container.dataset.clientFinish
            );


          const planStart =
            parseDate(
              container.dataset.planStart
            );


          const planFinish =
            parseDate(
              container.dataset.planFinish
            );


          const planWorkingDays =
            parseWorkingDays(
              container.dataset.planWorkingDays
            );


          container.colSpan =
            dates.length;


          container.style.position =
            "relative";


          container.style.overflow =
            "hidden";


          const wrapper =
            container.querySelector(
              ".planning-layer-grid"
            );


          if (
            !wrapper
          ) {
            return;
          }


          wrapper.innerHTML =
            "";


          const timelineWidth =
            dates.length
            *
            CELL_WIDTH;


          wrapper.style.width =
            timelineWidth
            +
            "px";


          wrapper.style.minWidth =
            timelineWidth
            +
            "px";


          wrapper.style.maxWidth =
            timelineWidth
            +
            "px";


          wrapper.style.position =
            "relative";


          wrapper.style.display =
            "flex";


          wrapper.style.flexDirection =
            "column";


          wrapper.style.justifyContent =
            "center";


          wrapper.style.overflow =
            "hidden";


          /*
           * Full-height calendar background.
           *
           * The day grid is independent from Baseline / Plan / Real.
           * This makes Sunday shading and day separators occupy the
           * complete activity-row height.
           */
          const backgroundGrid =
            document.createElement(
              "div"
            );


          backgroundGrid.className =
            "planning-calendar-background absolute inset-0 flex pointer-events-none z-0";


          backgroundGrid.style.width =
            timelineWidth
            +
            "px";


          backgroundGrid.style.minWidth =
            timelineWidth
            +
            "px";


          backgroundGrid.style.maxWidth =
            timelineWidth
            +
            "px";


          dates.forEach(
            function (date) {

              backgroundGrid.appendChild(
                createTimelineBackgroundCell(
                  date
                )
              );

            }
          );


          wrapper.appendChild(
            backgroundGrid
          );


          /*
           * Actual planning bars.
           */
          const layersContainer =
            document.createElement(
              "div"
            );


          layersContainer.className =
            "planning-layers-container relative z-10 flex flex-col justify-center";


          layersContainer.style.width =
            timelineWidth
            +
            "px";


          layersContainer.style.minWidth =
            timelineWidth
            +
            "px";


          layersContainer.style.maxWidth =
            timelineWidth
            +
            "px";


          const layers = [
            {
              type: "baseline",
              start: clientStart,
              finish: clientFinish,
              workingDays: null,
            },
            {
              type: "plan",
              start: planStart,
              finish: planFinish,
              workingDays: planWorkingDays,
            },
            {
              type: "real",
              start: null,
              finish: null,
              workingDays: null,
            },
          ];


          layers.forEach(
            function (layer) {

              const hasData =
                Boolean(
                  layer.start
                  &&
                  layer.finish
                );


              const layerRow =
                document.createElement(
                  "div"
                );


              layerRow.className =
                "planning-layer-row flex";


              layerRow.dataset.layer =
                layer.type;


              layerRow.dataset.hasData =
                hasData
                  ?
                  "1"
                  :
                  "0";


              if (
                !hasData
              ) {

                layerRow.classList.add(
                  "hidden"
                );

              } else {

                layerRow.classList.add(
                  "h-5"
                );

              }


              layerRow.style.width =
                timelineWidth
                +
                "px";


              layerRow.style.minWidth =
                timelineWidth
                +
                "px";


              layerRow.style.maxWidth =
                timelineWidth
                +
                "px";


              if (
                hasData
              ) {

                dates.forEach(
                  function (date) {

                    layerRow.appendChild(
                      createLayerCell(
                        date,
                        layer.start,
                        layer.finish,
                        layer.type,
                        layer.workingDays
                      )
                    );

                  }
                );

              }


              layersContainer.appendChild(
                layerRow
              );

            }
          );


          wrapper.appendChild(
            layersContainer
          );

        }
      );


      document.dispatchEvent(
        new CustomEvent(
          "master-plan-timeline-rebuilt"
        )
      );


      scheduleMatrixHeightSync();

    }


    /* =========================================================
       SCROLL TIMELINE TO DATE
    ========================================================== */

    function scrollTimelineToDate(
      timelineStart,
      targetDate
    ) {

      if (
        !timelineScroll
        ||
        !timelineStart
        ||
        !targetDate
      ) {
        return;
      }


      const timelineStartUTC =
        Date.UTC(
          timelineStart.getFullYear(),
          timelineStart.getMonth(),
          timelineStart.getDate()
        );


      const targetUTC =
        Date.UTC(
          targetDate.getFullYear(),
          targetDate.getMonth(),
          targetDate.getDate()
        );


      const differenceDays =
        Math.round(
          (
            targetUTC
            -
            timelineStartUTC
          )
          /
          86400000
        );


      const targetLeft =
        Math.max(
          0,
          differenceDays
          *
          CELL_WIDTH
        );


      timelineScroll.scrollLeft =
        targetLeft;

    }


    /* =========================================================
       RENDER EXPLICIT RANGE
    ========================================================== */

    function renderExplicitRange(
      startDate,
      endDate
    ) {

      if (
        !startDate
        ||
        !endDate
        ||
        endDate < startDate
      ) {
        return;
      }


      const dates =
        buildTimelineHeader(
          startDate,
          endDate
        );


      buildActivityTimelines(
        dates
      );


      window.requestAnimationFrame(
        function () {

          if (
            timelineScroll
          ) {

            timelineScroll.scrollLeft =
              0;

          }


          scheduleMatrixHeightSync();

        }
      );

    }


    /* =========================================================
       RENDER AUTO RANGE
    ========================================================== */

    function renderAutomaticTimeline() {

      timelineMode =
        "auto";


      setActivePreset(
        null
      );


      const bounds =
        getAutomaticPlanningBounds();


      const dates =
        buildTimelineHeader(
          bounds.start,
          bounds.end
        );


      buildActivityTimelines(
        dates
      );


      window.requestAnimationFrame(
        function () {

          if (
            !timelineScroll
          ) {
            return;
          }


          if (
            currentMonday >= bounds.start
            &&
            currentMonday <= bounds.end
          ) {

            scrollTimelineToDate(
              bounds.start,
              currentMonday
            );

          } else if (
            currentMonday < bounds.start
          ) {

            timelineScroll.scrollLeft =
              0;

          } else {

            timelineScroll.scrollLeft =
              timelineScroll.scrollWidth
              -
              timelineScroll.clientWidth;

          }


          scheduleMatrixHeightSync();

        }
      );

    }


    /* =========================================================
       ACTIVE QUICK RANGE BUTTON
    ========================================================== */

    function setActivePreset(activeButton) {

      document.querySelectorAll(
        ".timeline-preset-btn"
      ).forEach(
        function (button) {

          button.classList.remove(
            "border-blue-600",
            "bg-blue-50",
            "text-blue-700",
            "hover:bg-blue-100"
          );


          button.classList.add(
            "border-gray-300",
            "bg-white",
            "text-gray-600",
            "hover:bg-gray-50"
          );

        }
      );


      if (
        !activeButton
      ) {
        return;
      }


      activeButton.classList.remove(
        "border-gray-300",
        "bg-white",
        "text-gray-600",
        "hover:bg-gray-50"
      );


      activeButton.classList.add(
        "border-blue-600",
        "bg-blue-50",
        "text-blue-700",
        "hover:bg-blue-100"
      );

    }


    /* =========================================================
       SHOW CUSTOM RANGE
    ========================================================== */

    if (
      applyButton
    ) {

      applyButton.addEventListener(
        "click",
        function () {

          preservePosition(
            function () {

              const requestedStart =
                isoWeekToMonday(
                  fromWeekInput.value
                );


              const requestedEndMonday =
                isoWeekToMonday(
                  toWeekInput.value
                );


              if (
                !requestedStart
                ||
                !requestedEndMonday
              ) {
                return;
              }


              const requestedEnd =
                addDays(
                  requestedEndMonday,
                  6
                );


              if (
                requestedEnd
                <
                requestedStart
              ) {
                return;
              }


              timelineMode =
                "custom";


              setActivePreset(
                null
              );


              renderExplicitRange(
                requestedStart,
                requestedEnd
              );

            }
          );

        }
      );

    }


    /* =========================================================
       QUICK RANGE
    ========================================================== */

    document.querySelectorAll(
      ".timeline-preset-btn"
    ).forEach(
      function (button) {

        button.addEventListener(
          "click",
          function () {

            preservePosition(
              function () {

                const weeks =
                  Number(
                    button.dataset.weeks
                  );


                if (
                  !Number.isFinite(
                    weeks
                  )
                  ||
                  weeks < 1
                ) {
                  return;
                }


                const start =
                  currentMonday;


                const end =
                  addDays(
                    start,
                    (
                      weeks
                      *
                      7
                    )
                    -
                    1
                  );


                fromWeekInput.value =
                  dateToWeekInput(
                    start
                  );


                toWeekInput.value =
                  dateToWeekInput(
                    getMonday(
                      end
                    )
                  );


                timelineMode =
                  "preset";


                setActivePreset(
                  button
                );


                renderExplicitRange(
                  start,
                  end
                );

              }
            );

          }
        );

      }
    );


    /* =========================================================
       WORK TYPE FILTER
    ========================================================== */

    function rowMatchesWorkType(
      row,
      workType
    ) {

      if (
        workType === "all"
      ) {
        return true;
      }


      const text =
        (
          row.dataset.activityText
          ||
          ""
        ).toLowerCase();


      if (
        workType === "cabling"
      ) {

        return (
          text.includes(
            "cabl"
          )
          ||
          text.includes(
            "fiber placement"
          )
          ||
          text.includes(
            "placement"
          )
        );

      }


      if (
        workType === "splicing"
      ) {

        return (
          text.includes(
            "splic"
          )
          ||
          text.includes(
            "test"
          )
        );

      }


      return true;

    }


    document.querySelectorAll(
      ".planning-work-type-btn"
    ).forEach(
      function (button) {

        button.addEventListener(
          "click",
          function () {

            preservePosition(
              function () {

                document.querySelectorAll(
                  ".planning-work-type-btn"
                ).forEach(
                  function (item) {

                    item.classList.remove(
                      "bg-gray-900",
                      "text-white"
                    );


                    item.classList.add(
                      "bg-gray-100",
                      "text-gray-600"
                    );

                  }
                );


                button.classList.remove(
                  "bg-gray-100",
                  "text-gray-600"
                );


                button.classList.add(
                  "bg-gray-900",
                  "text-white"
                );


                const workType =
                  button.dataset.workType;


                document.querySelectorAll(
                  ".planning-activity-row-fixed"
                ).forEach(
                  function (row) {

                    const rowIndex =
                      row.dataset.rowIndex;


                    const timelineRow =
                      document.querySelector(
                        ".planning-timeline-row[data-row-index='"
                        +
                        rowIndex
                        +
                        "']"
                      );


                    const visible =
                      rowMatchesWorkType(
                        row,
                        workType
                      );


                    if (
                      visible
                    ) {

                      row.classList.remove(
                        "hidden"
                      );


                      if (
                        timelineRow
                      ) {

                        timelineRow.classList.remove(
                          "hidden"
                        );

                      }

                    } else {

                      row.classList.add(
                        "hidden"
                      );


                      if (
                        timelineRow
                      ) {

                        timelineRow.classList.add(
                          "hidden"
                        );

                      }

                    }

                  }
                );


                if (
                  timelineMode
                  ===
                  "auto"
                ) {

                  renderAutomaticTimeline();

                } else {

                  scheduleMatrixHeightSync();

                }

              }
            );

          }
        );

      }
    );


    /* =========================================================
       PREVENT TIMELINE FROM CHANGING PAGE WIDTH
    ========================================================== */

    function constrainTimelineViewport() {

      if (
        !timelineViewport
        ||
        !timelineScroll
      ) {
        return;
      }


      timelineViewport.style.minWidth =
        "0";


      timelineViewport.style.maxWidth =
        "100%";


      timelineViewport.style.overflow =
        "hidden";


      timelineScroll.style.width =
        "100%";


      timelineScroll.style.minWidth =
        "0";


      timelineScroll.style.maxWidth =
        "100%";

    }


    constrainTimelineViewport();


    window.addEventListener(
      "resize",
      function () {

        constrainTimelineViewport();


        scheduleMatrixHeightSync();

      }
    );


    /* =========================================================
       INITIAL RENDER
    ========================================================== */

    renderAutomaticTimeline();


    scheduleMatrixHeightSync();

  }
);