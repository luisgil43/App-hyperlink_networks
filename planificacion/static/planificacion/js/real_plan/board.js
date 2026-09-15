(function () {
  "use strict";


  let draggedCard = null;
  let sourceZone = null;
  let sourceNextSibling = null;
  let dropIndicator = null;

  let dropStarted = false;
  let saveInProgress = false;
  let workspaceRequestInProgress = false;


  function getCsrfToken() {
    const name = "csrftoken";

    const cookies = document.cookie
      ? document.cookie.split(";")
      : [];

    for (
      let i = 0;
      i < cookies.length;
      i += 1
    ) {
      const cookie = cookies[i].trim();

      if (
        cookie.substring(
          0,
          name.length + 1
        )
        === name + "="
      ) {
        return decodeURIComponent(
          cookie.substring(
            name.length + 1
          )
        );
      }
    }

    return "";
  }


  function showToast(
    message,
    type
  ) {
    const existing = document.querySelector(
      ".real-plan-move-toast"
    );

    if (existing) {
      existing.remove();
    }

    const toast = document.createElement(
      "div"
    );

    toast.className =
      "real-plan-move-toast "
      + (
        type === "success"
          ? "real-plan-move-toast-success"
          : "real-plan-move-toast-error"
      );

    toast.textContent = message;

    document.body.appendChild(
      toast
    );

    window.setTimeout(
      function () {
        toast.remove();
      },
      3000
    );
  }


  function captureScrollPosition() {
    const boardScroll = document.getElementById(
      "real-plan-board-scroll"
    );

    return {
      boardLeft: (
        boardScroll
          ? boardScroll.scrollLeft
          : 0
      ),
      boardTop: (
        boardScroll
          ? boardScroll.scrollTop
          : 0
      ),
      pageX: window.scrollX,
      pageY: window.scrollY,
    };
  }


  function restoreScrollPosition(position) {
    if (!position) {
      return;
    }

    const boardScroll = document.getElementById(
      "real-plan-board-scroll"
    );

    if (boardScroll) {
      boardScroll.scrollLeft =
        position.boardLeft;

      boardScroll.scrollTop =
        position.boardTop;
    }

    window.scrollTo(
      position.pageX,
      position.pageY
    );
  }


  function resetBoardHorizontalPosition(
    position
  ) {
    const boardScroll = document.getElementById(
      "real-plan-board-scroll"
    );

    if (boardScroll) {
      boardScroll.scrollLeft = 0;

      boardScroll.scrollTop =
        position
          ? position.boardTop
          : 0;
    }

    if (position) {
      window.scrollTo(
        position.pageX,
        position.pageY
      );
    }
  }


  function normalizeWorkspaceUrl(
    rawUrl
  ) {
    return new URL(
      rawUrl,
      window.location.href
    );
  }


  async function loadWorkspace(
    rawUrl,
    options
  ) {
    if (workspaceRequestInProgress) {
      return;
    }

    const settings =
      options
      || {};

    const scrollMode =
      settings.scrollMode
      || "preserve";

    const updateHistory =
      settings.updateHistory
      !== false;

    const workspace = document.getElementById(
      "real-plan-workspace"
    );

    if (!workspace) {
      window.location.href = rawUrl;
      return;
    }

    const position =
      captureScrollPosition();

    const url =
      normalizeWorkspaceUrl(
        rawUrl
      );

    workspaceRequestInProgress = true;

    workspace.classList.add(
      "is-loading"
    );

    try {
      const response = await fetch(
        url.toString(),
        {
          method: "GET",
          credentials: "same-origin",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
        }
      );

      if (!response.ok) {
        throw new Error(
          "Real Plan could not be refreshed."
        );
      }

      const html =
        await response.text();

      const parser =
        new DOMParser();

      const documentFragment =
        parser.parseFromString(
          html,
          "text/html"
        );

      const replacement =
        documentFragment.getElementById(
          "real-plan-workspace"
        );

      if (!replacement) {
        throw new Error(
          "Real Plan returned an invalid workspace."
        );
      }

      workspace.replaceWith(
        replacement
      );

      if (updateHistory) {
        window.history.pushState(
          {
            realPlan: true,
          },
          "",
          url.pathname
          + url.search
          + url.hash
        );
      }

      if (
        scrollMode === "start"
      ) {
        resetBoardHorizontalPosition(
          position
        );
      } else {
        restoreScrollPosition(
          position
        );
      }

    } catch (error) {
      workspace.classList.remove(
        "is-loading"
      );

      showToast(
        error.message
        || "Real Plan could not be refreshed.",
        "error"
      );

    } finally {
      workspaceRequestInProgress = false;
    }
  }


  function initializeWorkspaceNavigation() {
    document.addEventListener(
      "click",
      function (event) {
        const link = event.target.closest(
          "[data-real-plan-navigation]"
        );

        if (!link) {
          return;
        }

        if (
          event.metaKey
          || event.ctrlKey
          || event.shiftKey
          || event.altKey
        ) {
          return;
        }

        event.preventDefault();

        const href =
          link.getAttribute(
            "href"
          );

        if (!href) {
          return;
        }

        loadWorkspace(
          href,
          {
            scrollMode:
              link.dataset.realPlanScrollMode
              || "preserve",
          }
        );
      }
    );


    document.addEventListener(
      "submit",
      function (event) {
        const form = event.target.closest(
          "[data-real-plan-filter-form]"
        );

        if (!form) {
          return;
        }

        event.preventDefault();

        const formData =
          new FormData(
            form
          );

        const params =
          new URLSearchParams();

        formData.forEach(
          function (
            value,
            key
          ) {
            if (
              value !== null
              && String(value).trim() !== ""
            ) {
              params.append(
                key,
                value
              );
            }
          }
        );

        const url =
          window.location.pathname
          + "?"
          + params.toString();

        loadWorkspace(
          url,
          {
            scrollMode: "preserve",
          }
        );
      }
    );


    window.addEventListener(
      "popstate",
      function () {
        loadWorkspace(
          window.location.href,
          {
            scrollMode: "start",
            updateHistory: false,
          }
        );
      }
    );
  }


    function initializeStatusFilters() {
    document.addEventListener(
      "change",
      function (event) {
        const checkbox =
          event.target.closest(
            "input[name='statuses']"
          );

        if (!checkbox) {
          return;
        }

        const form =
          checkbox.closest(
            "[data-real-plan-filter-form]"
          );

        if (!form) {
          return;
        }

        const formData =
          new FormData(
            form
          );

        const params =
          new URLSearchParams();

        formData.forEach(
          function (
            value,
            key
          ) {
            if (
              value !== null
              && String(value).trim() !== ""
            ) {
              params.append(
                key,
                value
              );
            }
          }
        );

        const url =
          window.location.pathname
          + "?"
          + params.toString();

        loadWorkspace(
          url,
          {
            scrollMode: "preserve",
          }
        );
      }
    );
  }

  function initializeWeekSelector() {
    document.addEventListener(
      "change",
      function (event) {
        const selector =
          event.target.closest(
            "[data-real-plan-week-selector]"
          );

        if (!selector) {
          return;
        }

        const rawDate =
          (
            selector.value
            || ""
          ).trim();

        if (!rawDate) {
          return;
        }


        /*
         * Parse manually so timezone conversion
         * cannot move the selected calendar date.
         */
        const parts =
          rawDate.split("-");

        if (
          parts.length !== 3
        ) {
          return;
        }

        const year =
          Number(
            parts[0]
          );

        const month =
          Number(
            parts[1]
          );

        const day =
          Number(
            parts[2]
          );


        const selectedDate =
          new Date(
            year,
            month - 1,
            day
          );


        if (
          Number.isNaN(
            selectedDate.getTime()
          )
        ) {
          return;
        }


        /*
         * Real Plan always starts on Monday.
         *
         * JS:
         * Sunday = 0
         * Monday = 1
         * ...
         * Saturday = 6
         */
        const javascriptDay =
          selectedDate.getDay();


        const daysFromMonday =
          javascriptDay === 0
            ? 6
            : javascriptDay - 1;


        selectedDate.setDate(
          selectedDate.getDate()
          - daysFromMonday
        );


        const mondayYear =
          selectedDate.getFullYear();


        const mondayMonth =
          String(
            selectedDate.getMonth()
            + 1
          ).padStart(
            2,
            "0"
          );


        const mondayDay =
          String(
            selectedDate.getDate()
          ).padStart(
            2,
            "0"
          );


        const monday =
          mondayYear
          + "-"
          + mondayMonth
          + "-"
          + mondayDay;


        const params =
          new URLSearchParams();


        params.set(
          "week",
          monday
        );


        const search =
          (
            selector.dataset.realPlanSearch
            || ""
          ).trim();


        const workType =
          (
            selector.dataset.realPlanWorkType
            || "all"
          ).trim();


        const statusFilter =
          (
            selector.dataset.realPlanStatusFilter
            || "0"
          ).trim();


        const statuses =
          (
            selector.dataset.realPlanStatuses
            || ""
          )
            .split(",")
            .map(
              function (status) {
                return status.trim();
              }
            )
            .filter(
              function (status) {
                return Boolean(status);
              }
            );


        if (search) {
          params.set(
            "q",
            search
          );
        }


        if (
          workType
          && workType !== "all"
        ) {
          params.set(
            "work_type",
            workType
          );
        }


        if (
          statusFilter === "1"
        ) {
          params.set(
            "status_filter",
            "1"
          );

          statuses.forEach(
            function (status) {
              params.append(
                "statuses",
                status
              );
            }
          );
        }


        const url =
          window.location.pathname
          + "?"
          + params.toString();


        loadWorkspace(
          url,
          {
            scrollMode: "start",
          }
        );
      }
    );
  }

  function initializeCardDetails() {
    document.addEventListener(
      "click",
      function (event) {
        const toggle = event.target.closest(
          "[data-real-plan-card-toggle]"
        );

        if (!toggle) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();

        const card = toggle.closest(
          "[data-real-plan-card]"
        );

        if (!card) {
          return;
        }

        const details = card.querySelector(
          "[data-real-plan-card-details]"
        );

        if (!details) {
          return;
        }

        details.classList.toggle(
          "hidden"
        );
      }
    );
  }


  function initializeRecalculateButton() {
    document.addEventListener(
      "click",
      function (event) {
        const button =
          event.target.closest(
            "[data-real-plan-recalculate]"
          );

        if (!button) {
          return;
        }

        event.preventDefault();

        window.alert(
          "Automatic rollover and capacity recalculation will be connected next."
        );
      }
    );
  }


  function cardCanMove(card) {
    return Boolean(
      card
      && card.dataset.canMove === "1"
    );
  }


  function getStack(zone) {
    if (!zone) {
      return null;
    }

    return zone.querySelector(
      "[data-real-plan-stack], .real-plan-card-stack"
    );
  }


  function getCardsInZone(zone) {
    const stack = getStack(
      zone
    );

    if (!stack) {
      return [];
    }

    return Array.from(
      stack.children
    ).filter(
      function (child) {
        return child.matches(
          "[data-real-plan-card]"
        );
      }
    );
  }


  function getOrder(zone) {
    return getCardsInZone(
      zone
    )
      .map(
        function (card) {
          return Number(
            card.dataset.billingId
          );
        }
      )
      .filter(
        function (value) {
          return Number.isInteger(
            value
          );
        }
      );
  }


  function ensureEmptyState(zone) {
    const stack = getStack(
      zone
    );

    if (!stack) {
      return;
    }

    const cards = getCardsInZone(
      zone
    );

    const empty = stack.querySelector(
      ":scope > [data-real-plan-empty]"
    );

    if (cards.length === 0) {
      if (!empty) {
        const placeholder =
          document.createElement(
            "div"
          );

        placeholder.className =
          "real-plan-empty-cell";

        placeholder.setAttribute(
          "data-real-plan-empty",
          ""
        );

        placeholder.textContent =
          "—";

        stack.appendChild(
          placeholder
        );
      }

      return;
    }

    if (empty) {
      empty.remove();
    }
  }


  function removeEmptyState(zone) {
    if (!zone) {
      return;
    }

    const empty = zone.querySelector(
      "[data-real-plan-empty]"
    );

    if (empty) {
      empty.remove();
    }
  }


  function isValidTarget(
    card,
    zone
  ) {
    if (
      !card
      || !zone
      || zone.dataset.realPlanZone !== "day"
    ) {
      return false;
    }

    const sourceRow = card.closest(
      ".real-plan-person-row"
    );

    const targetRow = zone.closest(
      ".real-plan-person-row"
    );

    if (
      !sourceRow
      || !targetRow
    ) {
      return false;
    }

    return (
      sourceRow.dataset.rowKey
      === targetRow.dataset.rowKey
    );
  }


  function clearDropState() {
    document.querySelectorAll(
      ".real-plan-drop-cell.is-drag-over, "
      + ".real-plan-drop-cell.is-drag-invalid"
    ).forEach(
      function (zone) {
        zone.classList.remove(
          "is-drag-over",
          "is-drag-invalid"
        );
      }
    );
  }


  function removeDropIndicator() {
    if (
      dropIndicator
      && dropIndicator.parentNode
    ) {
      dropIndicator.remove();
    }

    dropIndicator = null;
  }


  function createDropIndicator() {
    if (dropIndicator) {
      return dropIndicator;
    }

    dropIndicator =
      document.createElement(
        "div"
      );

    dropIndicator.className =
      "real-plan-drop-indicator";

    return dropIndicator;
  }


  function getInsertionReference(
    stack,
    clientY
  ) {
    const cards = Array.from(
      stack.querySelectorAll(
        ":scope > [data-real-plan-card]"
      )
    ).filter(
      function (card) {
        return card !== draggedCard;
      }
    );

    for (
      let i = 0;
      i < cards.length;
      i += 1
    ) {
      const card = cards[i];

      const rect =
        card.getBoundingClientRect();

      const midpoint =
        rect.top
        + (
          rect.height / 2
        );

      if (
        clientY < midpoint
      ) {
        return card;
      }
    }

    return null;
  }


  function positionDropIndicator(
    zone,
    clientY
  ) {
    const stack =
      getStack(
        zone
      );

    if (!stack) {
      return;
    }

    removeEmptyState(
      zone
    );

    const indicator =
      createDropIndicator();

    const reference =
      getInsertionReference(
        stack,
        clientY
      );

    if (reference) {
      stack.insertBefore(
        indicator,
        reference
      );
    } else {
      stack.appendChild(
        indicator
      );
    }
  }


  function restoreCard() {
    if (
      !draggedCard
      || !sourceZone
    ) {
      return;
    }

    const stack =
      getStack(
        sourceZone
      );

    if (!stack) {
      return;
    }

    removeEmptyState(
      sourceZone
    );

    if (
      sourceNextSibling
      && sourceNextSibling.parentNode
      === stack
    ) {
      stack.insertBefore(
        draggedCard,
        sourceNextSibling
      );
    } else {
      stack.appendChild(
        draggedCard
      );
    }

    ensureEmptyState(
      sourceZone
    );
  }


  async function persistMove(
    card,
    targetDate,
    sourceOrder,
    targetOrder
  ) {
    const url =
      card.dataset.moveUrl;

    if (!url) {
      throw new Error(
        "Move endpoint is not configured."
      );
    }

    const response = await fetch(
      url,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": getCsrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(
          {
            date: targetDate,
            source_order: sourceOrder,
            target_order: targetOrder,
          }
        ),
      }
    );

    let data = null;

    try {
      data =
        await response.json();
    } catch (error) {
      data = null;
    }

    if (
      !response.ok
      || !data
      || data.ok !== true
    ) {
      const message =
        (
          data
          && (
            data.message
            || data.error
          )
        )
          ? (
              data.message
              || data.error
            )
          : "The project could not be moved.";

      throw new Error(
        message
      );
    }

    return data;
  }


  function initializeDragAndDrop() {
    document.addEventListener(
      "dragstart",
      function (event) {
        if (saveInProgress) {
          event.preventDefault();
          return;
        }

        const card =
          event.target.closest(
            "[data-real-plan-card]"
          );

        if (!card) {
          return;
        }

        if (!cardCanMove(card)) {
          event.preventDefault();

          showToast(
            "This project is locked by its current status.",
            "error"
          );

          return;
        }

        draggedCard = card;

        sourceZone = card.closest(
          ".real-plan-drop-cell"
        );

        sourceNextSibling =
          card.nextElementSibling;

        dropStarted = false;

        card.classList.add(
          "is-dragging"
        );

        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed =
            "move";

          event.dataTransfer.setData(
            "text/plain",
            card.dataset.billingId
            || ""
          );
        }
      }
    );


    document.addEventListener(
      "dragover",
      function (event) {
        if (
          !draggedCard
          || saveInProgress
        ) {
          return;
        }

        const zone =
          event.target.closest(
            "[data-real-plan-zone='day']"
          );

        if (!zone) {
          return;
        }

        event.preventDefault();

        clearDropState();

        if (
          !isValidTarget(
            draggedCard,
            zone
          )
        ) {
          removeDropIndicator();

          zone.classList.add(
            "is-drag-invalid"
          );

          if (event.dataTransfer) {
            event.dataTransfer.dropEffect =
              "none";
          }

          return;
        }

        zone.classList.add(
          "is-drag-over"
        );

        positionDropIndicator(
          zone,
          event.clientY
        );

        if (event.dataTransfer) {
          event.dataTransfer.dropEffect =
            "move";
        }
      }
    );


    document.addEventListener(
      "drop",
      async function (event) {
        if (
          !draggedCard
          || saveInProgress
        ) {
          return;
        }

        const targetZone =
          event.target.closest(
            "[data-real-plan-zone='day']"
          );

        if (!targetZone) {
          return;
        }

        event.preventDefault();

        if (
          !isValidTarget(
            draggedCard,
            targetZone
          )
        ) {
          removeDropIndicator();

          clearDropState();

          showToast(
            "You can change the day or order, but not the assigned person or team from this board.",
            "error"
          );

          return;
        }

        dropStarted = true;
        saveInProgress = true;

        const scrollPosition =
          captureScrollPosition();

        const targetDate =
          targetZone.dataset.date;

        if (!targetDate) {
          saveInProgress = false;
          dropStarted = false;
          return;
        }

        const card =
          draggedCard;

        const originalSourceZone =
          sourceZone;

        const targetStack =
          getStack(
            targetZone
          );

        if (!targetStack) {
          saveInProgress = false;
          dropStarted = false;
          return;
        }

        removeEmptyState(
          targetZone
        );

        if (
          dropIndicator
          && dropIndicator.parentNode
          === targetStack
        ) {
          targetStack.insertBefore(
            card,
            dropIndicator
          );
        } else {
          targetStack.appendChild(
            card
          );
        }

        removeDropIndicator();

        ensureEmptyState(
          originalSourceZone
        );

        ensureEmptyState(
          targetZone
        );

        const sameZone =
          originalSourceZone
          === targetZone;

        const sourceOrder =
          sameZone
            ? []
            : getOrder(
                originalSourceZone
              );

        const targetOrder =
          getOrder(
            targetZone
          );

        card.classList.remove(
          "is-dragging"
        );

        card.classList.add(
          "is-saving"
        );

        try {
          const result =
            await persistMove(
              card,
              targetDate,
              sourceOrder,
              targetOrder
            );

          card.dataset.currentDate =
            result.date;

          const detailDate =
            card.querySelector(
              "[data-real-plan-detail-date]"
            );

          if (detailDate) {
            detailDate.textContent =
              result.date;
          }

          ensureEmptyState(
            originalSourceZone
          );

          ensureEmptyState(
            targetZone
          );

          restoreScrollPosition(
            scrollPosition
          );

          showToast(
            sameZone
              ? "Project order updated."
              : "Project date and order updated.",
            "success"
          );

        } catch (error) {
          restoreCard();

          ensureEmptyState(
            originalSourceZone
          );

          ensureEmptyState(
            targetZone
          );

          restoreScrollPosition(
            scrollPosition
          );

          showToast(
            error.message
            || "The project could not be moved.",
            "error"
          );

        } finally {
          card.classList.remove(
            "is-saving"
          );

          removeDropIndicator();

          clearDropState();

          draggedCard = null;
          sourceZone = null;
          sourceNextSibling = null;

          saveInProgress = false;
          dropStarted = false;
        }
      }
    );


    document.addEventListener(
      "dragend",
      function () {
        /*
         * If drop already started, the async request
         * decides whether the card remains at destination
         * or returns to its original position.
         */
        if (
          dropStarted
          || saveInProgress
        ) {
          if (draggedCard) {
            draggedCard.classList.remove(
              "is-dragging"
            );
          }

          removeDropIndicator();

          clearDropState();

          return;
        }


        /*
         * No valid drop occurred.
         * Return the card to its original position.
         */
        if (draggedCard) {
          draggedCard.classList.remove(
            "is-dragging"
          );

          restoreCard();
        }

        removeDropIndicator();

        clearDropState();

        draggedCard = null;
        sourceZone = null;
        sourceNextSibling = null;
      }
    );
  }


  function initialize() {
    initializeWorkspaceNavigation();
    initializeStatusFilters();
    initializeWeekSelector();
    initializeCardDetails();
    initializeRecalculateButton();
    initializeDragAndDrop();
  }


  if (
    document.readyState === "loading"
  ) {
    document.addEventListener(
      "DOMContentLoaded",
      initialize
    );
  } else {
    initialize();
  }

})();